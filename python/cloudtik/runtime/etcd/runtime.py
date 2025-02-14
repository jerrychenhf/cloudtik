import logging
from typing import Any, Dict, Tuple, Optional

from cloudtik.core.node_provider import NodeProvider
from cloudtik.runtime.common.runtime_base import RuntimeBase
from cloudtik.runtime.etcd.utils import _get_runtime_processes, \
    _get_runtime_endpoints, _get_runtime_services, _with_runtime_environment_variables, \
    _get_runtime_logs, _handle_node_constraints_reached, _bootstrap_runtime_config

logger = logging.getLogger(__name__)


class EtcdRuntime(RuntimeBase):
    """Implementation of ETCD runtime for high available distributed kv store
    Hints:
    1. Checking status:
    etcdctl --endpoints=http://host:2379 member list
    2. Testing:
    etcdctl --endpoints=http://host:2379 put foo1 "Hello World!"
    etcdctl --endpoints=http://host:2379 get foo
    """

    def __init__(self, runtime_config: Dict[str, Any]) -> None:
        super().__init__(runtime_config)

    def bootstrap_config(
            self, cluster_config: Dict[str, Any]) -> Dict[str, Any]:
        """Final chance to update the config with runtime specific configurations
        This happens after provider bootstrap_config is done.
        """
        cluster_config = _bootstrap_runtime_config(cluster_config)
        return cluster_config

    def with_environment_variables(
            self, config: Dict[str, Any], provider: NodeProvider,
            node_id: str) -> Dict[str, Any]:
        """Export necessary runtime environment variables for running node commands.
        For example: {"ENV_NAME": value}
        """
        return _with_runtime_environment_variables(
            self.runtime_config, config=config)

    def get_node_constraints(
            self, cluster_config: Dict[str, Any],
            node_type: str) -> Tuple[bool, bool, bool]:
        """Whether the runtime nodes need minimal nodes launch before going to setup.
        Usually this is because the setup of the nodes need to know each other.
        """
        return True, True, True

    def node_constraints_reached(
            self, cluster_config: Dict[str, Any], node_type: str,
            head_info: Dict[str, Any], nodes_info: Dict[str, Any],
            quorum_id: Optional[str] = None):
        """If the get_node_constraints method returns True and runtime will be notified on head
        When the minimal nodes are reached. Please note this may call multiple times
        (for example server down and up)
        """
        _handle_node_constraints_reached(
            self.runtime_config, cluster_config,
            node_type, head_info, nodes_info)

    def get_runtime_endpoints(
            self, cluster_config: Dict[str, Any], cluster_head_ip: str):
        return _get_runtime_endpoints(
            self.runtime_config, cluster_config, cluster_head_ip)

    def get_runtime_services(self, cluster_config: Dict[str, Any]):
        return _get_runtime_services(self.runtime_config, cluster_config)

    @staticmethod
    def get_logs() -> Dict[str, str]:
        return _get_runtime_logs()

    @staticmethod
    def get_processes():
        return _get_runtime_processes()
