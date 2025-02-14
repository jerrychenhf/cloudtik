import click
import logging
import os
import subprocess
import time
from typing import Dict
from threading import Thread

from cloudtik.core._private.service_discovery.naming import with_node_host_environment_variables, \
    with_head_host_environment_variables
from cloudtik.core._private.utils import with_runtime_environment_variables, with_node_ip_environment_variables, \
    _get_cluster_uri, _is_use_internal_ip, get_node_type, get_runtime_shared_memory_ratio, \
    with_head_node_ip_environment_variables, get_default_python_version, get_config_option
from cloudtik.core.command_executor import get_cmd_to_print
from cloudtik.core.tags import CLOUDTIK_TAG_NODE_STATUS, CLOUDTIK_TAG_RUNTIME_CONFIG, \
    CLOUDTIK_TAG_FILE_MOUNTS_CONTENTS, \
    STATUS_UP_TO_DATE, STATUS_UPDATE_FAILED, STATUS_WAITING_FOR_SSH, \
    STATUS_SETTING_UP, STATUS_SYNCING_FILES, STATUS_BOOTSTRAPPING_DATA_DISKS, CLOUDTIK_TAG_NODE_SEQ_ID, \
    CLOUDTIK_TAG_QUORUM_JOIN, QUORUM_JOIN_STATUS_FAILED, QUORUM_JOIN_STATUS_SUCCESS
from cloudtik.core._private.subprocess_output_util import ProcessRunnerError
from cloudtik.core._private.log_timer import LogTimer
from cloudtik.core._private.cli_logger import cf, CliLogger
import cloudtik.core._private.subprocess_output_util as cmd_output_util
from cloudtik.core._private.constants import CLOUDTIK_RESOURCES_ENV, CLOUDTIK_RUNTIME_ENV_NODE_SEQ_ID, \
    CLOUDTIK_RUNTIME_ENV_NODE_TYPE, CLOUDTIK_RUNTIME_ENV_PROVIDER_TYPE, CLOUDTIK_RUNTIME_ENV_PYTHON_VERSION, \
    CLOUDTIK_NODE_START_WAIT_S, CLOUDTIK_RUNTIME_ENV_QUORUM_JOIN, \
    CLOUDTIK_RUNTIME_ENV_CLUSTER, CLOUDTIK_RUNTIME_ENV_NODE_ID, CLOUDTIK_RUNTIME_ENV_WORKSPACE, \
    CLOUDTIK_RUNTIME_ENV_NODE_IP, CLOUDTIK_BOOTSTRAP_CONFIG_FILE, CLOUDTIK_BOOTSTRAP_KEY_FILE, CLOUDTIK_RUNTIME_NAME
from cloudtik.core._private.event_system import (CreateClusterEvent, global_event_system)

logger = logging.getLogger(__name__)

NUM_SETUP_STEPS = 8
READY_CHECK_INTERVAL = 5

INITIALIZATION_COMMAND_DEFAULT_NUMBER_OF_RETRIES = 30
SETUP_COMMAND_DEFAULT_NUMBER_OF_RETRIES = 5
START_COMMAND_DEFAULT_NUMBER_OF_RETRIES = 3


class NodeUpdater:
    """A process for syncing files and running init commands on a node.

    Arguments:
        config: The cluster config for which the updator is running for
        call_context: the CallContext of this updater.
        node_id: the Node ID
        provider_config: Provider section of cluster config yaml
        provider: NodeProvider Class
        auth_config: Auth section of cluster config yaml
        cluster_name: the name of the cluster.
        file_mounts: Map of remote to local paths
        initialization_commands: Commands run before container launch
        setup_commands: Commands run before start commands
        start_commands: Commands to start cloudtik
        runtime_hash: Used to check for config changes
        file_mounts_contents_hash: Used to check for changes to file mounts
        is_head_node: Whether to use head start/setup commands
        rsync_options: Extra options related to the rsync command.
        process_runner: the module to use to run the commands
            in the CommandRunner. E.g., subprocess.
        use_internal_ip: Whether the node_id belongs to an internal ip
            or external ip.
        docker_config: Docker section of cluster config yaml
        restart_only: Whether to skip setup commands & just restart 
        for_recovery: True if updater is for a recovering node. Only used for
            metric tracking.
        runtime_config: The runtime configuration may be needed for running node commands
    """

    def __init__(
            self,
            config,
            call_context,
            node_id,
            provider_config,
            provider,
            auth_config,
            cluster_name,
            file_mounts,
            initialization_commands,
            setup_commands,
            start_commands,
            runtime_hash,
            file_mounts_contents_hash,
            is_head_node,
            node_resources=None,
            cluster_synced_files=None,
            rsync_options=None,
            process_runner=subprocess,
            use_internal_ip=False,
            docker_config=None,
            restart_only=False,
            for_recovery=False,
            runtime_config=None,
            environment_variables: Dict[str, object] = None):
        self.config = config
        self.call_context = call_context
        self.log_prefix = "NodeUpdater: {}: ".format(node_id)
        use_internal_ip = (use_internal_ip
                           or _is_use_internal_ip(provider_config))
        self.cmd_executor = provider.get_command_executor(
            self.call_context,
            self.log_prefix, node_id, auth_config, cluster_name,
            process_runner, use_internal_ip, docker_config)

        self.daemon = True
        self.node_id = node_id
        self.provider_type = provider_config.get("type")
        self.provider = provider
        # Some node providers don't specify empty structures as
        # defaults. Better to be defensive.
        file_mounts = file_mounts or {}
        self.file_mounts = {
            remote: os.path.expanduser(local)
            for remote, local in file_mounts.items()
        }

        self.initialization_commands = initialization_commands
        self.setup_commands = setup_commands
        self.start_commands = start_commands
        self.node_resources = node_resources
        self.runtime_hash = runtime_hash
        self.file_mounts_contents_hash = file_mounts_contents_hash
        # TODO (Alex): This makes the assumption that $HOME on the head and
        # worker nodes is the same. Also note that `cluster_synced_files` is
        # set on the head -> worker updaters only (so `expanduser` is only run
        # on the head node).
        cluster_synced_files = cluster_synced_files or []
        self.cluster_synced_files = [
            os.path.expanduser(path) for path in cluster_synced_files
        ]
        self.rsync_options = rsync_options or {}
        self.auth_config = auth_config
        self.is_head_node = is_head_node
        self.docker_config = docker_config
        self.restart_only = restart_only
        self.update_time = None
        self.for_recovery = for_recovery
        self.runtime_config = runtime_config
        self.cluster_uri = _get_cluster_uri(self.provider_type, cluster_name)
        self.environment_variables = environment_variables
        # Node Ip is set during the time for update
        self.node_ip = None
        self.exitcode = -1

    @property
    def cli_logger(self) -> CliLogger:
        return self.call_context.cli_logger

    def run(self):
        update_start_time = time.time()
        if self.call_context.does_allow_interactive(
        ) and self.call_context.is_output_redirected():
            # this is most probably a bug since the user has no control
            # over these settings
            msg = ("Output was redirected for an interactive command. "
                   "Either do not pass `--redirect-command-output` "
                   "or also pass in `--use-normal-shells`.")
            self.cli_logger.abort(
                self._prefix_message(msg))

        try:
            with LogTimer(self.log_prefix +
                          "Applied config {}".format(self.runtime_hash)):
                self.do_update()
        except Exception as e:
            tags_to_set = {CLOUDTIK_TAG_NODE_STATUS: STATUS_UPDATE_FAILED}
            node_tags = self.provider.node_tags(self.node_id)
            if CLOUDTIK_TAG_QUORUM_JOIN in node_tags:
                tags_to_set[CLOUDTIK_TAG_QUORUM_JOIN] = QUORUM_JOIN_STATUS_FAILED
            self.provider.set_node_tags(
                self.node_id, tags_to_set)
            self.cli_logger.error(
                self._prefix_message(
                    "New status: {}"), cf.bold(STATUS_UPDATE_FAILED))

            self.cli_logger.error("!!!")
            if hasattr(e, "cmd"):
                self.cli_logger.error(
                    self._prefix_message(
                        "Setup command `{}` failed with exit code {}. stderr:"),
                    cf.bold(e.cmd), e.returncode)
            else:
                self.cli_logger.verbose_error("{}", str(vars(e)))
                # TODO: handle this better somehow?
                self.cli_logger.error(
                    self._prefix_message("{}"), str(e))
            # TODO: print stderr here
            self.cli_logger.error("!!!")
            self.cli_logger.newline()

            if isinstance(e, click.ClickException):
                # TODO: why do we ignore this here
                return
            raise

        tags_to_set = {
            CLOUDTIK_TAG_NODE_STATUS: STATUS_UP_TO_DATE,
            CLOUDTIK_TAG_RUNTIME_CONFIG: self.runtime_hash,
        }
        node_tags = self.provider.node_tags(self.node_id)
        if CLOUDTIK_TAG_QUORUM_JOIN in node_tags:
            tags_to_set[CLOUDTIK_TAG_QUORUM_JOIN] = QUORUM_JOIN_STATUS_SUCCESS
        if self.file_mounts_contents_hash is not None:
            tags_to_set[
                CLOUDTIK_TAG_FILE_MOUNTS_CONTENTS] = self.file_mounts_contents_hash

        self.provider.set_node_tags(self.node_id, tags_to_set)
        self.cli_logger.labeled_value(
            self._prefix_message(
                "New status"), STATUS_UP_TO_DATE)

        self.update_time = time.time() - update_start_time
        self.exitcode = 0

    def sync_file_mounts(self, sync_cmd, step_numbers=(1, 2)):
        # step_numbers is (# of previous steps, total steps)
        current_step, total_steps = step_numbers

        nolog_paths = []
        if self.cli_logger.verbosity == 0:
            nolog_paths = [
                CLOUDTIK_BOOTSTRAP_KEY_FILE, CLOUDTIK_BOOTSTRAP_CONFIG_FILE
            ]

        def do_sync(remote_path, local_path, allow_non_existing_paths=False):
            if allow_non_existing_paths and not os.path.exists(local_path):
                self.cli_logger.print(
                    self._prefix_message(
                        "sync: {} does not exist. Skipping."), local_path)
                # Ignore missing source files. In the future we should support
                # the --delete-missing-args command to delete files that have
                # been removed
                return

            assert os.path.exists(local_path), local_path

            if os.path.isdir(local_path):
                if not local_path.endswith("/"):
                    local_path += "/"
                if not remote_path.endswith("/"):
                    remote_path += "/"

            with LogTimer(self.log_prefix +
                          "Synced {} to {}".format(local_path, remote_path)):
                is_docker = (self.docker_config
                             and self.docker_config.get("enabled", False))
                if not is_docker:
                    # The DockerCommandRunner handles this internally.
                    self.cmd_executor.run(
                        "mkdir -p {}".format(os.path.dirname(remote_path)),
                        run_env="host")
                sync_cmd(
                    local_path, remote_path, docker_mount_if_possible=True)

                if remote_path not in nolog_paths:
                    # TODO: timed here?
                    self.cli_logger.print(
                        self._prefix_message(
                            "{} from {}"), cf.bold(remote_path), cf.bold(local_path))

        # Rsync file mounts
        with self.cli_logger.group(
                self._prefix_message(
                    "Processing file mounts"),
                _numbered=("[]", current_step, total_steps)):
            for remote_path, local_path in self.file_mounts.items():
                do_sync(remote_path, local_path)
            current_step += 1

        if self.cluster_synced_files:
            with self.cli_logger.group(
                    self._prefix_message(
                        "Processing worker file mounts"),
                    _numbered=("[]", current_step, total_steps)):
                self.cli_logger.print(
                    self._prefix_message(
                        "synced files: {}"), str(self.cluster_synced_files))
                for path in self.cluster_synced_files:
                    do_sync(path, path, allow_non_existing_paths=True)
                current_step += 1
        else:
            with self.cli_logger.group(
                    self._prefix_message(
                        "No worker file mounts to sync"),
                    _numbered=("[]", current_step, total_steps)):
                pass

    def wait_ready(self, deadline):
        with self.cli_logger.group(
                self._prefix_message(
                    "Waiting for SSH to become available"),
                _numbered=("[]", 1, NUM_SETUP_STEPS)):
            with LogTimer(self.log_prefix + "Got remote shell"):

                self.cli_logger.print(
                    self._prefix_message(
                        "Running `{}` as a test."), cf.bold("uptime"))
                first_conn_refused_time = None
                while True:
                    if time.time() > deadline:
                        raise Exception(
                            self._prefix_message(
                                "Waiting for node ready timeout."))
                    if self.provider.is_terminated(self.node_id):
                        raise Exception(
                            self._prefix_message(
                                "Waiting for node ready aborting because node "
                                "detected as terminated."))

                    try:
                        # Run outside of the container
                        self.cmd_executor.run(
                            "uptime", timeout=10, run_env="host")
                        self.cli_logger.success(
                            self._prefix_message("Success."))
                        return True
                    except ProcessRunnerError as e:
                        first_conn_refused_time = \
                            cmd_output_util.handle_ssh_fails(
                                e, first_conn_refused_time,
                                retry_interval=READY_CHECK_INTERVAL)
                        time.sleep(READY_CHECK_INTERVAL)
                    except Exception as e:
                        # TODO: we should not be ignoring
                        # exceptions if they get filtered properly
                        # (new style log + non-interactive shells)
                        #
                        # however threading this configuration state
                        # is a pain and I'm leaving it for later

                        retry_str = "(" + str(e) + ")"
                        if hasattr(e, "cmd"):
                            if isinstance(e.cmd, str):
                                cmd_ = e.cmd
                            elif isinstance(e.cmd, list):
                                cmd_ = " ".join(e.cmd)
                            else:
                                logger.debug(f"e.cmd type ({type(e.cmd)}) not "
                                             "list or str.")
                                cmd_ = str(e.cmd)
                            retry_str = "(Exit Status {}): {}".format(
                                e.returncode, cmd_)

                        self.cli_logger.print(
                            self._prefix_message(
                                "SSH still not available {}, "
                                "retrying in {} seconds."), cf.dimmed(retry_str),
                            cf.bold(str(READY_CHECK_INTERVAL)))

                        time.sleep(READY_CHECK_INTERVAL)

    def bootstrap_data_disks(self, step_numbers=(1, 1)):
        current_step, total_steps = step_numbers
        with self.cli_logger.group(
                self._prefix_message(
                    "Preparing data disks"),
                _numbered=("[]", current_step, total_steps)):
            self.cmd_executor.bootstrap_data_disks()

    def get_update_environment_variables(self):
        node_type = get_node_type(self.provider, self.node_id)
        node_envs = with_runtime_environment_variables(
            self.runtime_config, config=self.config,
            provider=self.provider, node_id=self.node_id)

        # Add node ip address environment variables
        ip_envs = with_node_ip_environment_variables(
            self.call_context, None, self.provider, self.node_id)
        if self.is_head_node:
            # set the node_ip as head ip for head node
            head_ip = ip_envs.get(CLOUDTIK_RUNTIME_ENV_NODE_IP)
            ip_envs = with_head_node_ip_environment_variables(head_ip, ip_envs)
        node_envs.update(ip_envs)

        if self.environment_variables is not None:
            node_envs.update(self.environment_variables)

        # head host
        node_envs = with_head_host_environment_variables(
            self.config, node_envs)

        node_tags = self.provider.node_tags(self.node_id)

        # Set node sequence id if there is one
        node_seq_id = node_tags.get(CLOUDTIK_TAG_NODE_SEQ_ID)
        if node_seq_id is not None:
            node_envs[CLOUDTIK_RUNTIME_ENV_NODE_SEQ_ID] = node_seq_id
        node_envs = with_node_host_environment_variables(
            self.config, node_seq_id, node_envs)

        # Set node quorum join flag
        quorum_join = node_tags.get(CLOUDTIK_TAG_QUORUM_JOIN)
        if quorum_join is not None:
            node_envs[CLOUDTIK_RUNTIME_ENV_QUORUM_JOIN] = quorum_join

        # With node type in the environment variables
        if node_type is not None:
            node_envs[CLOUDTIK_RUNTIME_ENV_NODE_TYPE] = node_type

        if self.provider_type is not None:
            node_envs[CLOUDTIK_RUNTIME_ENV_PROVIDER_TYPE] = self.provider_type

        # python version to use if no python environment are already created
        node_envs[CLOUDTIK_RUNTIME_ENV_PYTHON_VERSION] = get_default_python_version(
            self.config)
        node_envs[CLOUDTIK_RUNTIME_ENV_NODE_ID] = self.node_id

        workspace_name = self.config.get("workspace_name")
        if workspace_name:
            node_envs[CLOUDTIK_RUNTIME_ENV_WORKSPACE] = workspace_name
        cluster_name = self.config.get("cluster_name")
        if cluster_name:
            node_envs[CLOUDTIK_RUNTIME_ENV_CLUSTER] = cluster_name

        self.node_ip = node_envs[CLOUDTIK_RUNTIME_ENV_NODE_IP]
        return node_envs

    def get_shared_memory_ratio(self):
        node_type = get_node_type(self.provider, self.node_id)
        return get_runtime_shared_memory_ratio(
            self.runtime_config, config=self.config, node_type=node_type)

    def _prefix_message(self, msg):
        if self.is_head_node:
            return msg
        msg_prefix = self.node_ip if self.node_ip else self.node_id
        if not msg_prefix:
            return msg
        return str(msg_prefix) + ": " + msg

    def do_update(self):
        self.provider.set_node_tags(
            self.node_id, {CLOUDTIK_TAG_NODE_STATUS: STATUS_WAITING_FOR_SSH})
        self.cli_logger.labeled_value(
            self._prefix_message(
                "New status"), STATUS_WAITING_FOR_SSH)

        deadline = time.time() + CLOUDTIK_NODE_START_WAIT_S
        self.wait_ready(deadline)
        global_event_system.execute_callback(
            self.cluster_uri,
            CreateClusterEvent.ssh_control_acquired,
            {"node_id": self.node_id}
        )

        node_tags = self.provider.node_tags(self.node_id)
        logger.debug("Node tags: {}".format(str(node_tags)))

        # The share memory ratio for /dev/shm
        shared_memory_ratio = self.get_shared_memory_ratio()

        if node_tags.get(CLOUDTIK_TAG_RUNTIME_CONFIG) == self.runtime_hash:
            # When resuming from a stopped instance the runtime_hash may be the
            # same, but the container will not be started.
            init_required = self.cmd_executor.run_init(
                as_head=self.is_head_node,
                file_mounts=self.file_mounts,
                shared_memory_ratio=shared_memory_ratio,
                sync_run_yet=False)
            if init_required:
                node_tags[CLOUDTIK_TAG_RUNTIME_CONFIG] += "-invalidate"
                # This ensures that `setup_commands` are not removed
                self.restart_only = False

        if self.restart_only:
            self.setup_commands = []

        runtime_envs = self.get_update_environment_variables()

        # runtime_hash will only change whenever the user restarts
        # or updates their cluster with `get_or_create_head_node`
        if node_tags.get(CLOUDTIK_TAG_RUNTIME_CONFIG) == self.runtime_hash and (
                not self.file_mounts_contents_hash
                or node_tags.get(CLOUDTIK_TAG_FILE_MOUNTS_CONTENTS) ==
                self.file_mounts_contents_hash):
            # todo: we lie in the confirmation message since
            # full setup might be cancelled here
            self.cli_logger.print(
                self._prefix_message(
                    "Configuration already up to date, "
                    "skipping file mounts, initialization and setup commands."),
                _numbered=("[]", "2-7", NUM_SETUP_STEPS))

        else:
            self.cli_logger.print(
                self._prefix_message(
                    "Updating cluster configuration."),
                _tags=dict(hash=self.runtime_hash))

            # The first step is to format and mount the data disks on host machine
            self.provider.set_node_tags(
                self.node_id, {CLOUDTIK_TAG_NODE_STATUS: STATUS_BOOTSTRAPPING_DATA_DISKS})
            self.cli_logger.labeled_value(
                self._prefix_message(
                    "New status"), STATUS_BOOTSTRAPPING_DATA_DISKS)
            self.bootstrap_data_disks(step_numbers=(2, NUM_SETUP_STEPS))

            self.provider.set_node_tags(
                self.node_id, {CLOUDTIK_TAG_NODE_STATUS: STATUS_SYNCING_FILES})
            self.cli_logger.labeled_value(
                self._prefix_message(
                    "New status"), STATUS_SYNCING_FILES)
            self.sync_file_mounts(
                self.rsync_up, step_numbers=(3, NUM_SETUP_STEPS))

            # Only run setup commands if runtime_hash has changed because
            # we don't want to run setup_commands every time the head node
            # file_mounts folders have changed.
            if node_tags.get(CLOUDTIK_TAG_RUNTIME_CONFIG) != self.runtime_hash:
                # Run init commands
                self.provider.set_node_tags(
                    self.node_id, {CLOUDTIK_TAG_NODE_STATUS: STATUS_SETTING_UP})
                self.cli_logger.labeled_value(
                    self._prefix_message(
                        "New status"), STATUS_SETTING_UP)
                if self.initialization_commands:
                    with self.cli_logger.group(
                            self._prefix_message(
                                "Running initialization commands"),
                            _numbered=("[]", 5, NUM_SETUP_STEPS)):
                        self._exec_initialization_commands(runtime_envs)
                else:
                    self.cli_logger.print(
                        self._prefix_message(
                            "No initialization commands to run."),
                        _numbered=("[]", 5, NUM_SETUP_STEPS))
                with self.cli_logger.group(
                        self._prefix_message(
                            "Initializing runner"),
                        _numbered=("[]", 6, NUM_SETUP_STEPS)):
                    self.cmd_executor.run_init(
                        as_head=self.is_head_node,
                        file_mounts=self.file_mounts,
                        shared_memory_ratio=shared_memory_ratio,
                        sync_run_yet=True)
                if self.setup_commands:
                    with self.cli_logger.group(
                            self._prefix_message(
                                "Running setup commands"),
                            _numbered=("[]", 7, NUM_SETUP_STEPS)):
                        self._exec_setup_commands(runtime_envs)
                else:
                    self.cli_logger.print(
                        self._prefix_message(
                            "No setup commands to run."),
                        _numbered=("[]", 7, NUM_SETUP_STEPS))

        with self.cli_logger.group(
                self._prefix_message(
                    "Starting the cluster services"),
                _numbered=("[]", 8, NUM_SETUP_STEPS)):
            self._exec_start_commands(runtime_envs)

    def get_cmd_to_print(self, cmd):
        verbose = False if self.cli_logger.verbosity == 0 else True
        return get_cmd_to_print(cmd, verbose)

    def rsync_up(self, source, target, docker_mount_if_possible=False):
        options = {"docker_mount_if_possible": docker_mount_if_possible,
                   "rsync_exclude": self.rsync_options.get("rsync_exclude"),
                   "rsync_filter": self.rsync_options.get("rsync_filter")}
        self.cmd_executor.run_rsync_up(source, target, options=options)
        self.cli_logger.verbose(
            "`rsync`ed {} (local) to {} (remote)", cf.bold(source), cf.bold(target))

    def rsync_down(self, source, target, docker_mount_if_possible=False):
        options = {"docker_mount_if_possible": docker_mount_if_possible,
                   "rsync_exclude": self.rsync_options.get("rsync_exclude"),
                   "rsync_filter": self.rsync_options.get("rsync_filter")}
        self.cmd_executor.run_rsync_down(source, target, options=options)
        self.cli_logger.verbose(
            "`rsync`ed {} (remote) to {} (local)", cf.bold(source), cf.bold(target))

    def _exec_initialization_commands(self, runtime_envs):
        global_event_system.execute_callback(
            self.cluster_uri,
            CreateClusterEvent.run_initialization_cmd,
            {"node_id": self.node_id})
        with LogTimer(
                self.log_prefix + "Initialization commands",
                show_status=True):
            for command_group in self.initialization_commands:
                commands = command_group.get("commands", [])
                for cmd in commands:
                    self._exec_initialization_command(cmd, runtime_envs)

    def _exec_initialization_command(self, cmd, runtime_envs):
        global_event_system.execute_callback(
            self.cluster_uri,
            CreateClusterEvent.run_initialization_cmd,
            {"node_id": self.node_id, "command": cmd})
        try:
            # Overriding the existing SSHOptions class
            # with a new SSHOptions class that uses
            # this ssh_private_key as its only __init__
            # argument.
            # Run outside docker.
            self.cmd_executor.run_with_retry(
                cmd,
                environment_variables=runtime_envs,
                ssh_options_override_ssh_key=self.auth_config.get("ssh_private_key"),
                run_env="host",
                number_of_retries=get_config_option(
                    self.config, "number_of_retries",
                    INITIALIZATION_COMMAND_DEFAULT_NUMBER_OF_RETRIES),
                retry_interval=get_config_option(self.config, "retry_interval")
            )
        except ProcessRunnerError as e:
            if e.msg_type == "ssh_command_failed":
                self.cli_logger.error("Failed.")
                self.cli_logger.error(
                    "See above for stderr.")

            raise click.ClickException(
                self._prefix_message("Initialization command failed.")
            ) from None

    def _exec_setup_commands(self, runtime_envs):
        global_event_system.execute_callback(
            self.cluster_uri,
            CreateClusterEvent.run_setup_cmd,
            {"node_id": self.node_id})
        with LogTimer(
                self.log_prefix + "Setup commands",
                show_status=True):

            total = len(self.setup_commands)
            for i, command_group in enumerate(self.setup_commands):
                command_group_name = command_group.get("group_name", "")
                with self.cli_logger.group(
                        self._prefix_message(
                            "Setting up: {}"),
                        command_group_name,
                        _numbered=("()", i + 1, total)):
                    commands = command_group.get("commands", [])
                    if command_group_name == CLOUDTIK_RUNTIME_NAME:
                        # Use a single install message for all commands
                        cmd_to_print = "{} runtime install".format(command_group_name)
                        self.cli_logger.print(
                            cf.bold("- " + self._prefix_message("{}")), cmd_to_print)
                    for cmd in commands:
                        self._exec_setup_command(
                            command_group_name, cmd, runtime_envs)

    def _exec_setup_command(self, command_group_name, cmd, runtime_envs):
        global_event_system.execute_callback(
            self.cluster_uri,
            CreateClusterEvent.run_setup_cmd,
            {"node_id": self.node_id, "command": cmd})

        cmd_to_print = self.get_cmd_to_print(cmd)
        if command_group_name != CLOUDTIK_RUNTIME_NAME:
            self.cli_logger.print(
                cf.bold("- " + self._prefix_message("{}")), cmd_to_print)

        try:
            # Runs in the container if docker is in use
            if get_config_option(self.config, "retry_setup_command", True):
                self.cmd_executor.run_with_retry(
                    cmd, environment_variables=runtime_envs, run_env="auto",
                    number_of_retries=get_config_option(
                        self.config, "number_of_retries",
                        SETUP_COMMAND_DEFAULT_NUMBER_OF_RETRIES),
                    retry_interval=get_config_option(self.config, "retry_interval")
                )
            else:
                self.cmd_executor.run(cmd, environment_variables=runtime_envs, run_env="auto")
        except ProcessRunnerError as e:
            if e.msg_type == "ssh_command_failed":
                if command_group_name != CLOUDTIK_RUNTIME_NAME:
                    self.cli_logger.error("Failed.")
                else:
                    # since we don't print individual command, we print it when error
                    self.cli_logger.error("Failed: {}", cmd_to_print)
                self.cli_logger.error(
                    "See above for stderr.")

            raise click.ClickException(
                self._prefix_message("Setup command failed."))

    def _exec_start_commands(self, runtime_envs):
        global_event_system.execute_callback(
            self.cluster_uri,
            CreateClusterEvent.start_cloudtik_runtime,
            {"node_id": self.node_id})
        with LogTimer(
                self.log_prefix + "Start commands", show_status=True):
            total = len(self.start_commands)
            for i, command_group in enumerate(self.start_commands):
                command_group_name = command_group.get("group_name", "")
                with self.cli_logger.group(
                        self._prefix_message(
                            "Starting: {}"),
                        command_group_name,
                        _numbered=("()", i + 1, total)):
                    commands = command_group.get("commands", [])
                    for cmd in commands:
                        self._exec_start_command(cmd, runtime_envs)
        global_event_system.execute_callback(
            self.cluster_uri,
            CreateClusterEvent.start_cloudtik_runtime_completed,
            {"node_id": self.node_id})

    def _exec_start_command(self, cmd, runtime_envs):
        # Add a resource override env variable if needed:
        if self.provider_type == "onpremise":
            # Local NodeProvider doesn't need resource override.
            env_vars = {}
        elif self.node_resources:
            env_vars = {
                CLOUDTIK_RESOURCES_ENV: self.node_resources
            }
        else:
            env_vars = {}
        env_vars.update(runtime_envs)

        cmd_to_print = self.get_cmd_to_print(cmd)
        self.cli_logger.print(
            cf.bold("- " + self._prefix_message("{}")), cmd_to_print)

        try:
            old_redirected = self.call_context.is_output_redirected()
            self.call_context.set_output_redirected(False)
            # Runs in the container if docker is in use
            if get_config_option(self.config, "retry_start_command", True):
                self.cmd_executor.run_with_retry(
                    cmd,
                    environment_variables=runtime_envs,
                    run_env="auto",
                    number_of_retries=get_config_option(
                        self.config, "number_of_retries",
                        START_COMMAND_DEFAULT_NUMBER_OF_RETRIES),
                    retry_interval=get_config_option(self.config, "retry_interval")
                )
            else:
                self.cmd_executor.run(
                    cmd,
                    environment_variables=env_vars,
                    run_env="auto")
            self.call_context.set_output_redirected(old_redirected)
        except ProcessRunnerError as e:
            if e.msg_type == "ssh_command_failed":
                self.cli_logger.error("Failed.")
                self.cli_logger.error("See above for stderr.")

            raise click.ClickException(
                self._prefix_message("Start command failed."))

    def exec_commands(self, action_name, commands, envs):
        with LogTimer(
                self.log_prefix + "Exec commands", show_status=True):
            total = len(commands)
            for i, command_group in enumerate(commands):
                command_group_name = command_group.get("group_name", "")
                with self.cli_logger.group(
                        self._prefix_message(
                            "{}: {}"),
                        action_name,
                        command_group_name,
                        _numbered=("()", i + 1, total)):
                    commands = command_group.get("commands", [])
                    for cmd in commands:
                        self._exec_command(cmd, envs)

    def _exec_command(self, cmd, envs):
        env_vars = {}
        if envs:
            env_vars.update(envs)

        cmd_to_print = self.get_cmd_to_print(cmd)
        self.cli_logger.print(
            cf.bold("- " + self._prefix_message("{}")), cmd_to_print)

        try:
            # Runs in the container if docker is in use
            self.cmd_executor.run(
                cmd,
                environment_variables=env_vars,
                run_env="auto")
        except ProcessRunnerError as e:
            if e.msg_type == "ssh_command_failed":
                self.cli_logger.error("Failed.")
                self.cli_logger.error("See above for stderr.")

            raise click.ClickException(
                self._prefix_message("Exec command failed."))


class NodeUpdaterThread(NodeUpdater, Thread):
    def __init__(self, *args, **kwargs):
        Thread.__init__(self)
        NodeUpdater.__init__(self, *args, **kwargs)
