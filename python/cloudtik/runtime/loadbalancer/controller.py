import logging
import time

from cloudtik.core._private.service_discovery.utils import deserialize_service_selector
from cloudtik.core._private.util.core_utils import deserialize_config, get_json_object_hash
from cloudtik.runtime.common.active_standby_service import ActiveStandbyService
from cloudtik.runtime.common.service_discovery.consul import \
    get_service_address_of_node, get_labels_of_service_nodes
from cloudtik.runtime.common.service_discovery.discovery import query_services_with_nodes
from cloudtik.runtime.common.service_discovery.load_balancer import LOAD_BALANCER_SERVICE_DISCOVERY_LABEL_PORT, \
    LOAD_BALANCER_SERVICE_DISCOVERY_LABEL_PROTOCOL, LOAD_BALANCER_SERVICE_DISCOVERY_NAME_LABEL
from cloudtik.runtime.loadbalancer.provider_api import get_load_balancer_manager, LoadBalancerBackendService

logger = logging.getLogger(__name__)

# print every 30 minutes for repeating errors
LOG_ERROR_REPEAT_SECONDS = 30 * 60
DEFAULT_LOAD_BALANCER_PULL_INTERVAL = 10
DEFAULT_LOAD_BALANCER_LEADER_TTL = 10
DEFAULT_LOAD_BALANCER_LEADER_ELECT_DELAY = 5

LOAD_BALANCER_CONTROLLER_SERVICE_NAME = "load-balancer-controller"


class LoadBalancerController(ActiveStandbyService):
    """Pulling job for discovering backend services for LoadBalancer
    and update LoadBalancer using provider specific API"""

    def __init__(
            self,
            coordinator_url: str = None,
            interval=None,
            service_selector=None,
            provider_config=None,
            workspace_name=None):
        super().__init__(
            coordinator_url,
            LOAD_BALANCER_CONTROLLER_SERVICE_NAME,
            leader_ttl=DEFAULT_LOAD_BALANCER_LEADER_TTL,
            leader_elect_delay=DEFAULT_LOAD_BALANCER_LEADER_ELECT_DELAY)
        if not interval:
            interval = DEFAULT_LOAD_BALANCER_PULL_INTERVAL
        self.interval = interval
        self.service_selector = deserialize_service_selector(
            service_selector)
        self.provider_config = deserialize_config(
            provider_config) if provider_config else {}
        self.load_balancer_manager = get_load_balancer_manager(
            self.provider_config, workspace_name)
        self.log_repeat_errors = LOG_ERROR_REPEAT_SECONDS // interval
        self.last_error_str = None
        self.last_error_num = 0
        self.last_backend_config_hash = None

    def _run(self):
        self.update()
        time.sleep(self.interval)

    def update(self):
        try:
            self._update()
            if self.last_error_str is not None:
                # if this is a recover from many errors, we print a recovering message
                if self.last_error_num >= self.log_repeat_errors:
                    logger.info(
                        "Recovering from {} repeated errors.".format(self.last_error_num))
                self.last_error_str = None
        except Exception as e:
            error_str = str(e)
            if self.last_error_str != error_str:
                logger.exception(
                    "Error happened when pulling: " + error_str)
                self.last_error_str = error_str
                self.last_error_num = 1
            else:
                self.last_error_num += 1
                if self.last_error_num % self.log_repeat_errors == 0:
                    logger.error(
                        "Error happened {} times for pulling: {}".format(
                            self.last_error_num, error_str))

    def _update(self):
        selected_services = self._query_services()
        backends = {}
        for service_name, service_nodes in selected_services.items():
            backend_service = self.get_backend_service(
                service_name, service_nodes)
            backend_name = service_name
            backends[backend_name] = backend_service

        # Finally, rebuild the LoadBalancer configuration
        backend_config_hash = get_json_object_hash(backends)
        if backend_config_hash != self.last_backend_config_hash:
            # save config file and reload only when data changed
            self.load_balancer_manager.update(backends)
            self.last_backend_config_hash = backend_config_hash

    def _query_services(self):
        return query_services_with_nodes(self.service_selector)

    @staticmethod
    def get_backend_service(service_name, service_nodes):
        backend_servers = []
        for service_node in service_nodes:
            server_address = get_service_address_of_node(service_node)
            backend_servers.append(server_address)

        labels = get_labels_of_service_nodes(service_nodes)
        protocol = labels.get(LOAD_BALANCER_SERVICE_DISCOVERY_LABEL_PROTOCOL)
        port = labels.get(LOAD_BALANCER_SERVICE_DISCOVERY_LABEL_PORT)
        load_balancer_name = labels.get(LOAD_BALANCER_SERVICE_DISCOVERY_NAME_LABEL)
        return LoadBalancerBackendService(
            service_name, backend_servers,
            protocol=protocol, port=port,
            load_balancer_name=load_balancer_name)
