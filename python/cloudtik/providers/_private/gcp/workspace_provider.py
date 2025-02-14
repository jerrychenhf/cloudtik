import logging
from typing import Any, Dict, Optional

from cloudtik.providers._private.gcp.config import create_gcp_workspace, \
    delete_gcp_workspace, check_gcp_workspace_integrity, \
    get_workspace_head_nodes, list_gcp_clusters, bootstrap_gcp_workspace, check_gcp_workspace_existence, \
    get_gcp_workspace_info, update_gcp_workspace, list_gcp_storages, list_gcp_databases
from cloudtik.core._private.utils import get_running_head_node, check_workspace_name_format, get_node_provider_of
from cloudtik.core._private.util.core_utils import string_to_hex_string, string_from_hex_string
from cloudtik.core.tags import CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX, CLOUDTIK_GLOBAL_VARIABLE_KEY
from cloudtik.core.workspace_provider import WorkspaceProvider

GCP_WORKSPACE_NAME_MAX_LEN = 19

logger = logging.getLogger(__name__)


class GCPWorkspaceProvider(WorkspaceProvider):
    def __init__(self, provider_config, workspace_name):
        WorkspaceProvider.__init__(
            self, provider_config, workspace_name)

    def create_workspace(self, config):
        create_gcp_workspace(config)

    def delete_workspace(
            self, config,
            delete_managed_storage: bool = False,
            delete_managed_database: bool = False):
        delete_gcp_workspace(
            config, delete_managed_storage, delete_managed_database)

    def update_workspace(
            self, config: Dict[str, Any],
            delete_managed_storage: bool = False,
            delete_managed_database: bool = False):
        update_gcp_workspace(
            config, delete_managed_storage, delete_managed_database)

    def check_workspace_existence(self, config: Dict[str, Any]):
        return check_gcp_workspace_existence(config)

    def check_workspace_integrity(self, config):
        return check_gcp_workspace_integrity(config)

    def list_clusters(
            self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return list_gcp_clusters(config)

    def list_storages(
            self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return list_gcp_storages(config)

    def list_databases(
            self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return list_gcp_databases(config)

    def publish_global_variables(
            self, cluster_config: Dict[str, Any],
            global_variables: Dict[str, Any]):
        """
        The global variables implements as labels. The following basic restrictions apply to labels:
        Each resource can have multiple labels, up to a maximum of 64.
        Each label must be a key-value pair.
        Keys have a minimum length of 1 character and a maximum length of 63 characters, and cannot be empty.
        Values can be empty, and have a maximum length of 63 characters.
        Keys and values can contain only lowercase letters, numeric characters, underscores, and dashes.
        All characters must use UTF-8 encoding, and international characters are allowed.
        Keys must start with a lowercase letter or international character.
        """
        # Add prefix to the variables
        global_variables_prefixed = {}
        for name in global_variables:
            prefixed_name = CLOUDTIK_GLOBAL_VARIABLE_KEY.format(
                string_to_hex_string(name))
            global_variables_prefixed[prefixed_name] = string_to_hex_string(
                global_variables[name])

        provider = get_node_provider_of(cluster_config)
        head_node_id = get_running_head_node(cluster_config, provider)
        provider.set_node_tags(head_node_id, global_variables_prefixed)

    def subscribe_global_variables(self, cluster_config: Dict[str, Any]):
        global_variables = {}
        head_nodes = get_workspace_head_nodes(
            self.provider_config, self.workspace_name)
        for head in head_nodes:
            for key, value in head.get("labels", {}).items():
                if key.startswith(CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX):
                    global_variable_name = string_from_hex_string(
                        key[len(CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX):])
                    global_variables[global_variable_name] = string_from_hex_string(value)

        return global_variables

    def validate_config(self, provider_config: Dict[str, Any]):
        if len(self.workspace_name) > GCP_WORKSPACE_NAME_MAX_LEN or \
                not check_workspace_name_format(self.workspace_name):
            raise RuntimeError(
                "{} workspace name is between 1 and {} characters, "
                "and can only contain lowercase alphanumeric "
                "characters and dashes".format(
                    provider_config["type"], GCP_WORKSPACE_NAME_MAX_LEN))

    def get_workspace_info(self, config: Dict[str, Any]):
        return get_gcp_workspace_info(config)

    @staticmethod
    def bootstrap_workspace_config(config):
        return bootstrap_gcp_workspace(config)
