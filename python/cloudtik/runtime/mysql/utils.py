import os
import uuid
from typing import Any, Dict

from cloudtik.core._private.runtime_factory import BUILT_IN_RUNTIME_MYSQL
from cloudtik.core._private.service_discovery.utils import \
    get_canonical_service_name, define_runtime_service, \
    get_service_discovery_config, SERVICE_DISCOVERY_FEATURE_DATABASE
from cloudtik.core._private.util.database_utils import DATABASE_PORT_MYSQL_DEFAULT, DATABASE_PASSWORD_MYSQL_DEFAULT
from cloudtik.core._private.utils import RUNTIME_CONFIG_KEY, is_node_seq_id_enabled, enable_node_seq_id

RUNTIME_PROCESSES = [
        # The first element is the substring to filter.
        # The second element, if True, is to filter ps results by command name.
        # The third element is the process name.
        # The forth element, if node, the process should on all nodes,if head, the process should on head node.
        ["mysqld", True, "MySQL", "node"],
    ]

MYSQL_SERVICE_PORT_CONFIG_KEY = "port"
MYSQL_GROUP_REPLICATION_PORT_CONFIG_KEY = "group_replication_port"

MYSQL_CLUSTER_MODE_CONFIG_KEY = "cluster_mode"
MYSQL_CLUSTER_MODE_NONE = "none"
# replication with GTID
MYSQL_CLUSTER_MODE_REPLICATION = "replication"
# group replication
MYSQL_CLUSTER_MODE_GROUP_REPLICATION = "group_replication"

MYSQL_GROUP_REPLICATION_NAME_CONFIG_KEY = "group_replication_name"

MYSQL_ROOT_PASSWORD_CONFIG_KEY = "root_password"

MYSQL_DATABASE_CONFIG_KEY = "database"
MYSQL_DATABASE_NAME_CONFIG_KEY = "name"
MYSQL_DATABASE_USER_CONFIG_KEY = "user"
MYSQL_DATABASE_PASSWORD_CONFIG_KEY = "password"

MYSQL_SERVICE_NAME = BUILT_IN_RUNTIME_MYSQL
MYSQL_SERVICE_PORT_DEFAULT = DATABASE_PORT_MYSQL_DEFAULT
MYSQL_GROUP_REPLICATION_PORT_DEFAULT = 33061

MYSQL_ROOT_PASSWORD_DEFAULT = DATABASE_PASSWORD_MYSQL_DEFAULT


def _get_config(runtime_config: Dict[str, Any]):
    return runtime_config.get(BUILT_IN_RUNTIME_MYSQL, {})


def _get_service_port(mysql_config: Dict[str, Any]):
    return mysql_config.get(
        MYSQL_SERVICE_PORT_CONFIG_KEY, MYSQL_SERVICE_PORT_DEFAULT)


def _get_group_replication_port(mysql_config: Dict[str, Any]):
    return mysql_config.get(
        MYSQL_GROUP_REPLICATION_PORT_CONFIG_KEY, MYSQL_GROUP_REPLICATION_PORT_DEFAULT)


def _get_cluster_mode(mysql_config: Dict[str, Any]):
    return mysql_config.get(
        MYSQL_CLUSTER_MODE_CONFIG_KEY, MYSQL_CLUSTER_MODE_GROUP_REPLICATION)


def _get_group_replication_name(mysql_config: Dict[str, Any]):
    return mysql_config.get(
        MYSQL_GROUP_REPLICATION_NAME_CONFIG_KEY)


def _generate_group_replication_name(config: Dict[str, Any]):
    workspace_name = config["workspace_name"]
    cluster_name = config["cluster_name"]
    return str(uuid.uuid3(uuid.NAMESPACE_OID, workspace_name + cluster_name))


def _get_home_dir():
    return os.path.join(
        os.getenv("HOME"), "runtime", BUILT_IN_RUNTIME_MYSQL)


def _get_runtime_processes():
    return RUNTIME_PROCESSES


def _get_runtime_logs():
    home_dir = _get_home_dir()
    logs_dir = os.path.join(home_dir, "logs")
    return {"mysql": logs_dir}


def _bootstrap_runtime_config(
        runtime_config: Dict[str, Any],
        cluster_config: Dict[str, Any]) -> Dict[str, Any]:
    mysql_config = _get_config(runtime_config)
    cluster_mode = _get_cluster_mode(mysql_config)
    if cluster_mode != MYSQL_CLUSTER_MODE_NONE:
        # We must enable the node seq id (stable seq id is preferred)
        # But we don't enforce it.
        if not is_node_seq_id_enabled(cluster_config):
            enable_node_seq_id(cluster_config)

    return cluster_config


def _validate_config(config: Dict[str, Any]):
    runtime_config = config.get(RUNTIME_CONFIG_KEY)
    mysql_config = _get_config(runtime_config)

    database = mysql_config.get(MYSQL_DATABASE_CONFIG_KEY, {})
    user = database.get(MYSQL_DATABASE_USER_CONFIG_KEY)
    password = database.get(MYSQL_DATABASE_PASSWORD_CONFIG_KEY)
    if (user and not password) or (not user and password):
        raise ValueError("User and password must be both specified or not specified.")


def _with_runtime_environment_variables(
        runtime_config, config):
    runtime_envs = {}

    mysql_config = _get_config(runtime_config)

    service_port = _get_service_port(mysql_config)
    runtime_envs["MYSQL_SERVICE_PORT"] = service_port

    cluster_mode = _get_cluster_mode(mysql_config)
    runtime_envs["MYSQL_CLUSTER_MODE"] = cluster_mode

    if cluster_mode == MYSQL_CLUSTER_MODE_GROUP_REPLICATION:
        # configure the group replication GUID
        group_replication_name = _get_group_replication_name(mysql_config)
        if not group_replication_name:
            group_replication_name = _generate_group_replication_name(config)
        runtime_envs["MYSQL_GROUP_REPLICATION_NAME"] = group_replication_name

        group_replication_port = _get_group_replication_port(mysql_config)
        runtime_envs["MYSQL_GROUP_REPLICATION_PORT"] = group_replication_port

    root_password = mysql_config.get(
        MYSQL_ROOT_PASSWORD_CONFIG_KEY, MYSQL_ROOT_PASSWORD_DEFAULT)
    runtime_envs["MYSQL_ROOT_PASSWORD"] = root_password

    database = mysql_config.get(MYSQL_DATABASE_CONFIG_KEY, {})
    database_name = database.get(MYSQL_DATABASE_NAME_CONFIG_KEY)
    if database_name:
        runtime_envs["MYSQL_DATABASE"] = database_name
    user = database.get(MYSQL_DATABASE_USER_CONFIG_KEY)
    if user:
        runtime_envs["MYSQL_USER"] = user
    password = database.get(MYSQL_DATABASE_PASSWORD_CONFIG_KEY)
    if password:
        runtime_envs["MYSQL_PASSWORD"] = password

    return runtime_envs


def _get_runtime_endpoints(runtime_config: Dict[str, Any], cluster_head_ip):
    service_port = _get_service_port(runtime_config)
    endpoints = {
        "mysql": {
            "name": "MySQL",
            "url": "{}:{}".format(cluster_head_ip, service_port)
        },
    }
    return endpoints


def _get_head_service_ports(runtime_config: Dict[str, Any]) -> Dict[str, Any]:
    service_port = _get_service_port(runtime_config)
    service_ports = {
        "mysql": {
            "protocol": "TCP",
            "port": service_port,
        },
    }
    return service_ports


def _get_runtime_services(
        runtime_config: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
    mysql_config = _get_config(runtime_config)
    service_discovery_config = get_service_discovery_config(mysql_config)
    service_name = get_canonical_service_name(
        service_discovery_config, cluster_name, MYSQL_SERVICE_NAME)
    service_port = _get_service_port(mysql_config)
    services = {
        service_name: define_runtime_service(
            service_discovery_config, service_port,
            features=[SERVICE_DISCOVERY_FEATURE_DATABASE]),
    }
    return services
