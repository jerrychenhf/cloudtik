import logging
from typing import Any, Dict, Optional

from cloudtik.core._private.utils import get_running_head_node, check_workspace_name_format, get_node_provider_of
from cloudtik.providers._private.aliyun.config import create_aliyun_workspace, \
    delete_aliyun_workspace, check_aliyun_workspace_integrity, \
    list_aliyun_clusters, _get_workspace_head_nodes, bootstrap_aliyun_workspace, \
    check_aliyun_workspace_existence, get_aliyun_workspace_info, update_aliyun_workspace, list_aliyun_storages
from cloudtik.core.tags import CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX, CLOUDTIK_GLOBAL_VARIABLE_KEY
from cloudtik.core.workspace_provider import WorkspaceProvider

ALIYUN_WORKSPACE_NAME_MAX_LEN = 24

logger = logging.getLogger(__name__)


class AliyunWorkspaceProvider(WorkspaceProvider):
    def __init__(self, provider_config, workspace_name):
        WorkspaceProvider.__init__(self, provider_config, workspace_name)

    def create_workspace(self, config):
        create_aliyun_workspace(config)

    def delete_workspace(
            self, config,
            delete_managed_storage: bool = False,
            delete_managed_database: bool = False):
        delete_aliyun_workspace(config, delete_managed_storage)

    def update_workspace(
            self, config: Dict[str, Any],
            delete_managed_storage: bool = False,
            delete_managed_database: bool = False):
        update_aliyun_workspace(
            config, delete_managed_storage, delete_managed_database)

    def check_workspace_existence(self, config: Dict[str, Any]):
        return check_aliyun_workspace_existence(config)

    def check_workspace_integrity(self, config):
        return check_aliyun_workspace_integrity(config)

    def list_clusters(
            self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return list_aliyun_clusters(config)

    def list_storages(
            self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return list_aliyun_storages(config)

    def publish_global_variables(
            self, cluster_config: Dict[str, Any],
            global_variables: Dict[str, Any]):
        """
        The global variables implements as tags. The following basic restrictions apply to tags:
        Maximum number of tags that can be added to a single resource: 20
        Tag key: A tag key must be 1 to 128 characters in length and cannot contain http:// or https://.
        It cannot start with aliyun or acs:.
        Tag value: A tag value must be 1 to 128 characters in length and cannot contain http:// or https://.
        It cannot start with aliyun or acs:.
        """

        # Add prefix to the variables
        global_variables_prefixed = {}
        for name in global_variables:
            prefixed_name = CLOUDTIK_GLOBAL_VARIABLE_KEY.format(name)
            global_variables_prefixed[prefixed_name] = global_variables[name]

        provider = get_node_provider_of(cluster_config)
        head_node_id = get_running_head_node(cluster_config, provider)
        provider.set_node_tags(head_node_id, global_variables_prefixed)

    def subscribe_global_variables(self, cluster_config: Dict[str, Any]):
        global_variables = {}
        head_nodes = _get_workspace_head_nodes(
            self.provider_config, self.workspace_name)
        for head in head_nodes:
            for tag in head.tags.tag:
                tag_key = tag.tag_key
                if tag_key.startswith(CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX):
                    global_variable_name = tag_key[len(CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX):]
                    global_variables[global_variable_name] = tag.tag_value

        return global_variables

    def validate_config(self, provider_config: Dict[str, Any]):
        if len(self.workspace_name) > ALIYUN_WORKSPACE_NAME_MAX_LEN or \
                not check_workspace_name_format(self.workspace_name):
            raise RuntimeError(
                "{} workspace name is between 1 and {} characters, "
                "and can only contain lowercase alphanumeric "
                "characters and dashes".format(
                    provider_config["type"], ALIYUN_WORKSPACE_NAME_MAX_LEN))

    def get_workspace_info(self, config: Dict[str, Any]):
        return get_aliyun_workspace_info(config)

    @staticmethod
    def bootstrap_workspace_config(config):
        return bootstrap_aliyun_workspace(config)
