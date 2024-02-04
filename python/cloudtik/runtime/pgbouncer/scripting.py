import os
import shutil
import signal
from shlex import quote

from cloudtik.core._private.runtime_factory import BUILT_IN_RUNTIME_PGBOUNCER
from cloudtik.core._private.service_discovery.utils import include_runtime_service_for_selector, \
    serialize_service_selector, get_service_selector_copy
from cloudtik.core._private.util.core_utils import open_with_mode, exec_with_output, \
    kill_process_by_pid_file, is_file_changed, get_config_for_update
from cloudtik.core._private.util.database_utils import get_database_username_with_default, \
    get_database_password_with_default, \
    get_database_address, get_database_port, get_database_name, set_database_config, DATABASE_ENGINE_POSTGRES, \
    get_database_username, DATABASE_CONFIG_USERNAME, DATABASE_CONFIG_DATABASE
from cloudtik.core._private.util.runtime_utils import get_runtime_config_from_node, get_runtime_node_address_type
from cloudtik.runtime.common.service_discovery.runtime_discovery import DATABASE_SERVICE_SELECTOR_KEY
from cloudtik.runtime.pgbouncer.utils import _get_config, _get_home_dir, _get_backend_databases, _get_backend_config, \
    _is_database_bind_user, _get_database_connect, _get_database_auth_user, \
    _get_database_auth_password, _get_logs_dir, PGBOUNCER_DISCOVER_POSTGRES_SERVICE_TYPES, \
    PGBOUNCER_DATABASE_CONNECT_CONFIG_KEY, _get_admin_user, _get_admin_password, _get_dynamic_config, \
    PGBOUNCER_DATABASE_AUTH_USER_CONFIG_KEY, PGBOUNCER_DATABASE_BIND_USER_CONFIG_KEY

PGBOUNCER_PULL_BACKENDS_INTERVAL = 15


###################################
# Calls from node when configuring
###################################


def _get_config_dir():
    home_dir = _get_home_dir()
    return os.path.join(home_dir, "conf")


def _get_config_file():
    return os.path.join(_get_config_dir(), "pgbouncer.ini")


def _get_auth_file():
    return os.path.join(_get_config_dir(), "userlist.txt")


def _get_pid_file():
    home_dir = _get_home_dir()
    return os.path.join(home_dir, "run", "pgbouncer.pid")


def configure_backend(head):
    runtime_config = get_runtime_config_from_node(head)
    pgbouncer_config = _get_config(runtime_config)
    backend_config = _get_backend_config(pgbouncer_config)
    # no matter static or dynamic, we need to the backend servers
    backend_databases = _get_backend_databases(backend_config)

    (username_password_map,
     username_password_conflicts) = _get_username_password_info(
        pgbouncer_config, backend_databases)
    _configure_auth(username_password_map)
    _configure_databases(backend_databases, username_password_conflicts)


def _configure_auth(username_password_map):
    auth_file = _get_auth_file()
    _update_auth_file(
        auth_file, username_password_map)


def _configure_databases(
        backend_databases, username_password_conflicts):
    conf_dir = _get_config_dir()
    config_file_template = os.path.join(
        conf_dir, "pgbouncer-template.ini")
    config_file_working = os.path.join(
        conf_dir, "pgbouncer-working.ini")
    shutil.copyfile(config_file_template, config_file_working)
    _update_backends(
        config_file_working, backend_databases, username_password_conflicts)

    config_file = _get_config_file()
    if not is_file_changed(config_file_working, config_file):
        return False
    shutil.move(config_file_working, config_file)
    return True


def _add_user_password(
        username_password_map, username_password_conflicts,
        username, password):
    if not username:
        return
    if username in username_password_conflicts:
        return
    if username not in username_password_map:
        username_password_map[username] = password
    else:
        existing_password = username_password_map[username]
        if existing_password != password:
            # conflicts
            username_password_map.pop(username)
            username_password_conflicts.add(username)


def _add_connect_username_password(
        username_password_map, username_password_conflicts,
        database_connect):
    if not database_connect:
        return
    username = get_database_username_with_default(database_connect)
    password = get_database_password_with_default(database_connect)
    _add_user_password(
        username_password_map, username_password_conflicts,
        username, password)


def _add_auth_username_password(
        username_password_map, database_config):
    # WARNING: we should avoid auth user password conflicts
    username = _get_database_auth_user(database_config)
    password = _get_database_auth_password(database_config)
    if not username or not password:
        return
    username_password_map[username] = password


def add_database_config_username_password(
        username_password_map, auth_user_password_map,
        username_password_conflicts, database_config):
    database_connect = _get_database_connect(database_config)
    _add_connect_username_password(
        username_password_map, username_password_conflicts,
        database_connect)
    _add_auth_username_password(
        auth_user_password_map, database_config)


def _get_username_password_info(
        pgbouncer_config, backend_databases):
    username_password_map = {}
    auth_user_password_map = {}
    username_password_conflicts = set()
    for _, database_config in backend_databases.items():
        add_database_config_username_password(
            username_password_map, auth_user_password_map,
            username_password_conflicts, database_config)

    # dynamic username and passwords
    backend_config = _get_backend_config(pgbouncer_config)
    dynamic_config = _get_dynamic_config(backend_config)
    if dynamic_config:
        add_database_config_username_password(
            username_password_map, auth_user_password_map,
            username_password_conflicts, dynamic_config)

    # Make sure auth user password always appear
    username_password_map.update(auth_user_password_map)
    # Make sure admin user and password always appear
    admin_user = _get_admin_user(pgbouncer_config)
    admin_password = _get_admin_password(pgbouncer_config)
    username_password_map[admin_user] = admin_password
    return username_password_map, username_password_conflicts


def _escape_auth_value(auth_value):
    # replace each " with ""
    return auth_value.replace("\"", "\"\"")


def _update_auth_file(auth_file, username_password_map):
    # "username1" "password"
    # There should be at least 2 fields, surrounded by double quotes.
    # The first field is the username and the second is either a plain-text,
    # a MD5-hashed password, or a SCRAM secret.
    # PgBouncer ignores the rest of the line. Double quotes in a field value
    # can be escaped by writing two double quotes.
    user_password_lines = []
    # for same username but different passwords, we need put the password in the connect line
    for username, password in username_password_map.items():
        escaped_username = _escape_auth_value(username)
        escaped_password = _escape_auth_value(password)
        user_password_line = f"\"{escaped_username}\" \"{escaped_password}\""
        user_password_lines.append(user_password_line)
    user_password_block = "\n".join(user_password_lines)
    with open_with_mode(auth_file, "w", os_mode=0o600) as f:
        f.write(user_password_block)
        f.write("\n")


def _get_backend_database_lines(
        backend_databases, username_password_conflicts):
    backend_database_lines = []
    for database_name, database_config in backend_databases.items():
        backend_database_line = _get_backend_database_line(
            database_name, database_config, username_password_conflicts)
        if backend_database_line:
            backend_database_lines.append(backend_database_line)
    return backend_database_lines


def _get_backend_database_line(
        database_name, database_config, username_password_conflicts):
    database_connect = _get_database_connect(database_config)
    host = get_database_address(database_connect)
    if not host:
        return None

    port = get_database_port(database_connect)
    connect_str = f"host={host} port={port}"

    db_name = get_database_name(database_connect)
    if db_name:
        connect_str += f" dbname={db_name}"

    if _is_database_bind_user(database_config):
        username = get_database_username_with_default(database_connect)
        connect_str += f" user={username}"
        if username in username_password_conflicts:
            # need use password in the connect line
            password = get_database_password_with_default(database_connect)
            connect_str += f" password={password}"

    auth_user = _get_database_auth_user(database_config)
    if auth_user:
        connect_str += f" auth_user={auth_user}"

    # Only available on 1.22.0 upwards
    """
    auth_query = _get_database_auth_query(database_config)
    if auth_query:
        # Simple query such as: SELECT usename, passwd FROM pg_shadow WHERE usename=$1
        connect_str += f" auth_query='{auth_query}'"
    """

    connect_line = "{} = {}".format(database_name, connect_str)
    return connect_line


def _update_backends(config_file, backend_databases, username_password_conflicts):
    # append the database connect string params at the end
    backend_database_lines = _get_backend_database_lines(
        backend_databases, username_password_conflicts)
    backend_databases_block = "\n".join(backend_database_lines)
    with open(config_file, "a") as f:
        f.write(backend_databases_block)
        f.write("\n")


def _get_pull_identifier():
    return "{}-discovery".format(BUILT_IN_RUNTIME_PGBOUNCER)


def start_pull_server(head):
    runtime_config = get_runtime_config_from_node(head)
    pgbouncer_config = _get_config(runtime_config)
    backend_config = _get_backend_config(pgbouncer_config)
    dynamic_config = _get_dynamic_config(backend_config)

    pull_identifier = _get_pull_identifier()
    logs_dir = _get_logs_dir()

    service_selector = get_service_selector_copy(
        pgbouncer_config, DATABASE_SERVICE_SELECTOR_KEY)

    service_selector = include_runtime_service_for_selector(
        service_selector,
        service_type=PGBOUNCER_DISCOVER_POSTGRES_SERVICE_TYPES)

    service_selector_str = serialize_service_selector(service_selector)
    address_type = get_runtime_node_address_type()

    cmd = ["cloudtik", "node", "pull", pull_identifier, "start"]
    cmd += ["--pull-class=cloudtik.runtime.pgbouncer.discovery.DiscoverBackendServers"]
    cmd += ["--interval={}".format(
        PGBOUNCER_PULL_BACKENDS_INTERVAL)]
    cmd += ["--logs-dir={}".format(quote(logs_dir))]

    # job parameters
    if service_selector_str:
        cmd += ["service_selector={}".format(service_selector_str)]
    cmd += ["address_type={}".format(str(address_type))]

    if dynamic_config:
        database_connect = _get_database_connect(dynamic_config)
        db_user = get_database_username(database_connect)
        if db_user:
            cmd += ["db_user={}".format(db_user)]
        db_name = get_database_name(database_connect)
        if db_name:
            cmd += ["db_name={}".format(db_name)]
        auth_user = _get_database_auth_user(dynamic_config)
        if auth_user:
            cmd += ["auth_user={}".format(auth_user)]
        bind_user = _is_database_bind_user(dynamic_config)
        if bind_user:
            cmd += ["bind_user=true"]

    cmd_str = " ".join(cmd)
    exec_with_output(cmd_str)


def stop_pull_server():
    pull_identifier = _get_pull_identifier()
    cmd = ["cloudtik", "node", "pull", pull_identifier, "stop"]
    cmd_str = " ".join(cmd)
    exec_with_output(cmd_str)


def update_configuration(
        services,
        db_user=None,
        db_name=None,
        auth_user=None,
        bind_user=None):
    backend_databases = _get_databases_from_services(
        services, db_user, db_name,
        auth_user, bind_user)
    # for dynamic cases, we will not have conflicts as there should be single to share for all
    username_password_conflicts = set()
    if _configure_databases(
            backend_databases, username_password_conflicts):
        # the conf is changed, reload the service by sending a SIGHUP with kill
        pid_file = _get_pid_file()
        kill_process_by_pid_file(pid_file, sig=signal.SIGHUP)


def _get_databases_from_services(
        services,
        db_user=None,
        db_name=None,
        auth_user=None,
        bind_user=None):
    backend_databases = {}
    for service_name, service_instance in services:
        database_name = get_database_name_from_service_name(
            service_name)
        database_config = get_database_config_from_service(
            service_instance, db_user, db_name,
            auth_user, bind_user)
        backend_databases[database_name] = database_config
    return backend_databases


def get_database_name_from_service_name(service_name):
    database_name = service_name.replace("-", "_")
    return database_name


def get_database_config_from_service(
        service_instance,
        db_user=None,
        db_name=None,
        auth_user=None,
        bind_user=None):
    database_config = {}
    database_connect = get_config_for_update(
        database_config, PGBOUNCER_DATABASE_CONNECT_CONFIG_KEY)

    service_addresses = service_instance.service_addresses
    database_service = (DATABASE_ENGINE_POSTGRES, service_addresses)
    set_database_config(database_connect, database_service)

    # set other options from global settings
    if db_user:
        database_connect[DATABASE_CONFIG_USERNAME] = db_user
    if db_name:
        database_connect[DATABASE_CONFIG_DATABASE] = db_name
    if auth_user:
        database_config[PGBOUNCER_DATABASE_AUTH_USER_CONFIG_KEY] = auth_user
    if bind_user:
        database_config[PGBOUNCER_DATABASE_BIND_USER_CONFIG_KEY] = bind_user
    return database_config