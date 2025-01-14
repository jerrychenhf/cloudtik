from typing import Optional
from urllib.parse import quote

from cloudtik.core._private.service_discovery.naming import get_cluster_node_fqdn, get_cluster_node_sqdn
from cloudtik.core._private.service_discovery.utils import SERVICE_SELECTOR_SERVICES, SERVICE_SELECTOR_TAGS, \
    SERVICE_SELECTOR_LABELS, SERVICE_SELECTOR_EXCLUDE_LABELS, SERVICE_DISCOVERY_LABEL_CLUSTER, \
    SERVICE_SELECTOR_RUNTIMES, SERVICE_SELECTOR_CLUSTERS, SERVICE_SELECTOR_EXCLUDE_JOINED_LABELS, \
    SERVICE_DISCOVERY_TAG_CLUSTER_PREFIX, SERVICE_DISCOVERY_TAG_SYSTEM_PREFIX, ServiceAddressType, \
    SERVICE_DISCOVERY_LABEL_RUNTIME, SERVICE_SELECTOR_SERVICE_TYPES, SERVICE_DISCOVERY_LABEL_SERVICE, \
    SERVICE_DISCOVERY_LABEL_SEQ_ID, SERVICE_DISCOVERY_LABEL_NODE_ID
from cloudtik.core._private.util.core_utils import get_intersect_labels
from cloudtik.core._private.util.rest_api import EndPointAddress
from cloudtik.runtime.common.consul_utils import consul_api_get, ConsulClient
from cloudtik.runtime.common.service_discovery.utils import ServiceInstance

REST_ENDPOINT_CATALOG = "/v1/catalog"
REST_ENDPOINT_CATALOG_SERVICES = REST_ENDPOINT_CATALOG + "/services"
REST_ENDPOINT_CATALOG_SERVICE = REST_ENDPOINT_CATALOG + "/service"


def get_expressions_of_service_selector(service_selector):
    if not service_selector:
        return None

    services = service_selector.get(SERVICE_SELECTOR_SERVICES)
    service_types = service_selector.get(SERVICE_SELECTOR_SERVICE_TYPES)
    tags = service_selector.get(SERVICE_SELECTOR_TAGS)
    labels = service_selector.get(SERVICE_SELECTOR_LABELS)
    runtimes = service_selector.get(SERVICE_SELECTOR_RUNTIMES)
    clusters = service_selector.get(SERVICE_SELECTOR_CLUSTERS)
    exclude_labels = service_selector.get(SERVICE_SELECTOR_EXCLUDE_LABELS)
    exclude_joined_labels = service_selector.get(
        SERVICE_SELECTOR_EXCLUDE_JOINED_LABELS)

    if not (services or service_types or
            tags or labels or
            runtimes or clusters or
            exclude_labels or exclude_joined_labels):
        return None

    expressions = []
    if services:
        # Any services in the list (OR)
        service_expressions = []
        for service_name in services:
            service_expressions.append(
                'ServiceName == "{}"'.format(service_name))
        expressions.append(" or ".join(service_expressions))

    if tags:
        # Services must contain all the tags (AND)
        tag_expressions = []
        for tag in tags:
            tag_expressions.append('"{}" in ServiceTags'.format(tag))
        expressions.append(" and ".join(tag_expressions))

    if labels:
        # All labels must match (AND)
        label_expressions = []
        for label_name, label_value in labels.items():
            # TODO: shall we anchor to the start and end to value by default?
            label_expressions.append(
                'ServiceMeta["{}"] matches "{}"'.format(
                    label_name, label_value))
        expressions.append(" and ".join(label_expressions))

    if service_types:
        # Services of any these types will be included (OR)
        service_type_expressions = []
        for service_type in service_types:
            service_type_expressions.append(
                'ServiceMeta["{}"] == "{}"'.format(
                    SERVICE_DISCOVERY_LABEL_SERVICE, service_type))
        expressions.append(" or ".join(service_type_expressions))

    if runtimes:
        # Services of any these runtimes will be included (OR)
        runtime_expressions = []
        for runtime in runtimes:
            runtime_expressions.append(
                'ServiceMeta["{}"] == "{}"'.format(
                    SERVICE_DISCOVERY_LABEL_RUNTIME, runtime))
        expressions.append(" or ".join(runtime_expressions))

    if clusters:
        # Services of any these clusters will be included (OR)
        cluster_expressions = []
        for cluster in clusters:
            cluster_expressions.append(
                'ServiceMeta["{}"] == "{}"'.format(
                    SERVICE_DISCOVERY_LABEL_CLUSTER, cluster))
        expressions.append(" or ".join(cluster_expressions))

    if exclude_labels:
        # Services with any matched labels will be excluded [NOT (OR)] or [AND NOT]
        exclude_label_expressions = []
        for label_name, label_value in exclude_labels.items():
            exclude_label_expressions.append(
                'ServiceMeta["{}"] not matches "{}"'.format(
                    label_name, label_value))
        expressions.append(" and ".join(exclude_label_expressions))

    if exclude_joined_labels:
        # Services with all matched labels will be excluded [NOT (AND)] or [OR NOT]
        exclude_joined_expressions = []
        for joined_labels in exclude_joined_labels:
            # all the labels must match for each joined labels
            joined_label_expressions = []
            for label_name, label_value in joined_labels.items():
                joined_label_expressions.append(
                    'ServiceMeta["{}"] not matches "{}"'.format(
                        label_name, label_value))
            exclude_joined_expressions.append(
                " or ".join(joined_label_expressions))
        expressions.append(" and ".join(["( {} )".format(
            expr) for expr in exclude_joined_expressions]))

    return " and ".join(["( {} )".format(expr) for expr in expressions])


def _get_endpoint_with_service_selector(base_endpoint, service_selector):
    expressions = get_expressions_of_service_selector(service_selector)
    if expressions:
        encoded_expressions = quote(expressions)
        query_filter = "filter=" + encoded_expressions
        endpoint = "{}?{}".format(
            base_endpoint, query_filter)
    else:
        endpoint = base_endpoint
    return endpoint


def query_services(
        service_selector, address: Optional[EndPointAddress] = None):
    # query all the services with a service selector
    query_endpoint = _get_endpoint_with_service_selector(
        REST_ENDPOINT_CATALOG_SERVICES, service_selector)

    client = ConsulClient(address)
    # The response is a dictionary of services with the value is the list of tags
    return consul_api_get(client, query_endpoint)


def query_service_nodes(
        service_name, service_selector,
        address: Optional[EndPointAddress] = None):
    service_endpoint = "{}/{}".format(
            REST_ENDPOINT_CATALOG_SERVICE, service_name)
    query_endpoint = _get_endpoint_with_service_selector(
        service_endpoint, service_selector)

    client = ConsulClient(address)
    # The response is a list of server nodes of this service
    return consul_api_get(client, query_endpoint)


def get_service_name_of_node(service_node):
    return service_node["ServiceName"]


def get_service_address_of_node(
        service_node,
        address_type: ServiceAddressType = ServiceAddressType.NODE_IP):
    if address_type == ServiceAddressType.NODE_IP:
        service_host = service_node.get("ServiceAddress")
        if not service_host:
            service_host = service_node.get("Address")
    elif address_type == ServiceAddressType.NODE_SQDN:
        # short domain name
        node_name = service_node.get("Node")
        service_host = get_cluster_node_sqdn(node_name)
    else:
        # use the node DNS FQDN: <node>.node[.<datacenter>.dc].<domain>
        node_name = service_node.get("Node")
        datacenter = service_node.get("Datacenter")
        service_host = get_dns_hostname_of_node(node_name, datacenter)

    port = service_node["ServicePort"]
    return service_host, port


def get_dns_hostname_of_node(node_name, workspace_name):
    return get_cluster_node_fqdn(node_name, workspace_name)


def get_service_cluster_of_node(service_node):
    # This is our service implementation specific
    # each service will be labeled with its cluster name
    return get_service_label_of_node(
        service_node, SERVICE_DISCOVERY_LABEL_CLUSTER)


def get_service_runtime_of_node(service_node):
    # This is our service implementation specific
    # each service will be labeled with its runtime name
    return get_service_label_of_node(
        service_node, SERVICE_DISCOVERY_LABEL_RUNTIME)


def get_service_label_of_node(service_node, label_name):
    service_meta = service_node.get(
        "ServiceMeta", {})
    return service_meta.get(label_name)


def get_node_label_of_node(service_node, label_name):
    node_meta = service_node.get(
        "NodeMeta", {})
    return node_meta.get(label_name)


def get_node_seq_id_of_node(service_node):
    return get_node_label_of_node(
        service_node, SERVICE_DISCOVERY_LABEL_SEQ_ID)


def get_node_id_of_node(service_node):
    return get_node_label_of_node(
        service_node, SERVICE_DISCOVERY_LABEL_NODE_ID)


def get_service_dns_name(
        service_name, service_tag=None, service_cluster=None):
    if service_tag and service_cluster:
        raise ValueError(
            "You can either specify service tag or service cluster. But not both.")
    elif not service_tag and not service_cluster:
        return "{}.service.cloudtik".format(service_name)
    elif service_tag:
        return "{}.{}.service.cloudtik".format(service_tag, service_name)
    else:
        return "{}{}.{}.service.cloudtik".format(
            SERVICE_DISCOVERY_TAG_CLUSTER_PREFIX, service_cluster, service_name)


def get_rfc2782_service_dns_name(
        service_name, service_tag=None, service_cluster=None):
    # The service dns name format of RFC 2782 lookup
    # SRV queries must prepend service and protocol values with an underscore (_)
    # or _<service>._<protocol>[.service][.<datacenter>].<domain>
    # or _<service>._<tag>[.service][.<datacenter>].<domain>
    if service_tag and service_cluster:
        raise ValueError(
            "You can either specify service tag or service cluster. But not both.")
    elif not service_tag and not service_cluster:
        return "_{}._tcp.service.cloudtik".format(service_name)
    elif service_tag:
        return "_{}._{}.service.cloudtik".format(service_name, service_tag)
    else:
        return "_{}._{}{}.service.cloudtik".format(
            service_name, SERVICE_DISCOVERY_TAG_CLUSTER_PREFIX, service_cluster)


def select_dns_service_tag(tags):
    if not tags:
        return None

    # select cluster tag if exists, otherwise a user tag
    user_tag = None
    for tag in tags:
        if tag.startswith(
                SERVICE_DISCOVERY_TAG_CLUSTER_PREFIX):
            return tag
        elif not user_tag and not tag.startswith(
                SERVICE_DISCOVERY_TAG_SYSTEM_PREFIX):
            user_tag = tag
    if user_tag:
        return user_tag

    return tags[0]


def get_service_fqdn_address(service_name, service_tags):
    service_tag = select_dns_service_tag(service_tags)
    return get_service_dns_name(service_name, service_tag)


def get_common_label_of_service_nodes(
        service_nodes, label_name, error_if_not_same=False):
    # return the common label value of all the service nodes
    # return None or raise error if it is not the same
    if not service_nodes:
        return None
    service_node = service_nodes[0]
    common_label_value = get_service_label_of_node(
            service_node, label_name)
    for service_node in service_nodes[1:]:
        label_value = get_service_label_of_node(
            service_node, label_name)
        if label_value != common_label_value:
            if error_if_not_same:
                raise RuntimeError(
                    "Label {} has more than one value in server nodes.".format(label_name))
            else:
                return None
    return common_label_value


def _get_cluster_of_service_nodes(
        service_nodes, error_if_not_same=False):
    return get_common_label_of_service_nodes(
        service_nodes, SERVICE_DISCOVERY_LABEL_CLUSTER,
        error_if_not_same=error_if_not_same)


def _get_runtime_of_service_nodes(
        service_nodes, error_if_not_same=False):
    return get_common_label_of_service_nodes(
        service_nodes, SERVICE_DISCOVERY_LABEL_RUNTIME,
        error_if_not_same=error_if_not_same)


def _get_service_type_of_service_nodes(
        service_nodes, error_if_not_same=False):
    return get_common_label_of_service_nodes(
        service_nodes, SERVICE_DISCOVERY_LABEL_SERVICE,
        error_if_not_same=error_if_not_same)


def get_tags_of_service_nodes(service_nodes):
    if not service_nodes:
        return []

    service_node = service_nodes[0]
    # get a common set of tags
    tags = set(service_node.get("ServiceTags", []))
    if not tags:
        return []

    for service_node in service_nodes[1:]:
        service_tags = set(service_node.get("ServiceTags", []))
        tags = tags.intersection(service_tags)
        if not tags:
            return []
    return list(tags)


def get_labels_of_service_nodes(service_nodes):
    if not service_nodes:
        return {}

    service_node = service_nodes[0]
    # get a common set of labels
    labels = service_node.get("ServiceMeta")
    if not labels:
        return {}

    for service_node in service_nodes[1:]:
        service_labels = service_node.get("ServiceMeta")
        labels = get_intersect_labels(labels, service_labels)
        if not labels:
            return {}
    return labels


def get_addresses_of_service_nodes(
        service_nodes,
        address_type: ServiceAddressType = ServiceAddressType.NODE_IP,):
    return [get_service_address_of_node(
        service_node, address_type=address_type) for service_node in service_nodes]


def query_service(
        service_name, service_selector,
        address_type: ServiceAddressType = ServiceAddressType.NODE_IP,
        address: Optional[EndPointAddress] = None):
    service_nodes = query_service_nodes(
        service_name, service_selector, address=address)
    if not service_nodes:
        return None

    # Common labels
    labels = get_labels_of_service_nodes(service_nodes)
    # the runtime of the service nodes usually be the same
    # different for the cases that different runtimes may provide the same service types
    runtime_type = labels.get(SERVICE_DISCOVERY_LABEL_RUNTIME)
    # Usually service type should be the same
    service_type = labels.get(SERVICE_DISCOVERY_LABEL_SERVICE)
    # WARNING: the cluster of the service nodes may not be the same. None if not the same
    cluster_name = labels.get(SERVICE_DISCOVERY_LABEL_CLUSTER)

    if address_type == ServiceAddressType.SERVICE_FQDN:
        # return service FQDN
        # the service tags include tags filtered by service selector
        # But it doesn't guarantee every service node has all the service tags
        # Here we find the common tags among all the nodes.
        # If there is no common tags, it implies user doesn't get correctly on
        # tags and service selectors. Or it is just what user really want.
        tags = get_tags_of_service_nodes(service_nodes)
        service_addresses = [get_service_fqdn_address(
            service_name, tags)]
    else:
        # return service nodes IP or nodes FQDN
        service_addresses = get_addresses_of_service_nodes(
            service_nodes, address_type=address_type)

    return ServiceInstance(
        service_name, service_addresses,
        service_type=service_type,
        runtime_type=runtime_type,
        cluster_name=cluster_name,
        labels=labels)


def query_services_from_consul(
        service_selector,
        address_type: ServiceAddressType = ServiceAddressType.NODE_IP,
        address: Optional[EndPointAddress] = None,
        first: bool = False):
    services = query_services(service_selector, address=address)
    if not services:
        return None

    if first:
        service_name, service_tags = next(iter(services.items()))
        service_instance = query_service(
            service_name, service_selector,
            address_type=address_type, address=address
        )
        return service_instance
    else:
        services_to_return = {}
        for service_name, service_tags in services.items():
            service_instance = query_service(
                service_name, service_selector,
                address_type=address_type, address=address
            )
            if service_instance:
                services_to_return[service_name] = service_instance

        return services_to_return


def query_services_with_nodes(
        service_selector,
        address: Optional[EndPointAddress] = None,
        first: bool = False):
    services_with_nodes = {}
    services = query_services(service_selector, address=address)
    if not services:
        return services_with_nodes

    if first:
        service_name, service_tags = next(iter(services.items()))
        service_nodes = query_service_nodes(
            service_name, service_selector, address=address)
        if service_nodes:
            services_with_nodes[service_name] = service_nodes
    else:
        for service_name, service_tags in services.items():
            service_nodes = query_service_nodes(
                service_name, service_selector, address=address)
            if service_nodes:
                services_with_nodes[service_name] = service_nodes

    return services_with_nodes


def query_services_with_addresses(
        service_selector,
        address_type: ServiceAddressType = ServiceAddressType.NODE_IP,
        address: Optional[EndPointAddress] = None,
        first: bool = False):
    services_with_addresses = {}
    services = query_services(service_selector, address=address)
    if not services:
        return services_with_addresses

    if first:
        service_name, service_tags = next(iter(services.items()))
        service_nodes = query_service_nodes(
            service_name, service_selector, address=address)
        if service_nodes:
            service_addresses = get_addresses_of_service_nodes(
                service_nodes, address_type=address_type)
            services_with_addresses[service_name] = service_addresses
    else:
        for service_name, service_tags in services.items():
            service_nodes = query_service_nodes(
                service_name, service_selector, address=address)
            if service_nodes:
                service_addresses = get_addresses_of_service_nodes(
                    service_nodes, address_type=address_type)
                services_with_addresses[service_name] = service_addresses

    return services_with_addresses
