#!/bin/bash

# Current bin directory
BIN_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
ROOT_DIR="$(dirname "$(dirname "$BIN_DIR")")"

args=$(getopt -a -o h:: -l head:: -- "$@")
eval set -- "${args}"

USER_HOME=/home/$(whoami)
RUNTIME_PATH=$USER_HOME/runtime
HAPROXY_HOME=$RUNTIME_PATH/haproxy

# Util functions
. "$ROOT_DIR"/common/scripts/util-functions.sh

prepare_base_conf() {
    source_dir=$(dirname "${BIN_DIR}")/conf
    output_dir=/tmp/haproxy/conf
    rm -rf  $output_dir
    mkdir -p $output_dir
    cp -r $source_dir/* $output_dir
}

check_haproxy_installed() {
    if ! command -v haproxy &> /dev/null
    then
        echo "HAProxy is not installed."
        exit 1
    fi
}

configure_http_check() {
    local config_file="${1:?config file is required}"
    local http_check_option=""
    local http_check_send=""
    local http_check_port=""
    if [ "${HAPROXY_HTTP_CHECK}" == "true" ]; then
        local check_uri="/"
        if [ ! -z "${HAPROXY_HTTP_CHECK_PATH}" ]; then
            check_uri="${HAPROXY_HTTP_CHECK_PATH}"
        fi
        http_check_option="option httpchk"
        http_check_send="http-check send meth GET uri ${check_uri}"
        if [ ! -z "${HAPROXY_HTTP_CHECK_PORT}" ]; then
            http_check_port="port ${HAPROXY_HTTP_CHECK_PORT}"
        fi
    fi
    update_in_file "${config_file}" \
      "{%http.check.option%}" "${http_check_option}"
    update_in_file "${config_file}" \
      "{%http.check.send%}" "${http_check_send}"
    update_in_file "${config_file}" \
      "{%http.check.port%}" "${http_check_port}"
}

configure_dns_backend() {
    # configure a load balancer based on Consul DNS interface
    local config_template_file=${output_dir}/haproxy-dns-consul.cfg

    # Consul DNS interface based service discovery
    update_in_file "${config_template_file}" \
      "{%backend.max.servers%}" "${HAPROXY_BACKEND_MAX_SERVERS}"
    update_in_file "${config_template_file}" \
      "{%backend.service.dns.name%}" "${HAPROXY_BACKEND_SERVICE_DNS_NAME}"
    configure_http_check "${config_template_file}"

    cat ${config_template_file} >> ${haproxy_config_file}
}

configure_static_backend() {
    # configure a load balancer with static address
    local config_template_file=${output_dir}/haproxy-static.cfg
    configure_http_check "${config_template_file}"

    # python configure script will write the list of static servers
    cat ${config_template_file} >> ${haproxy_config_file}
}

configure_dynamic_backend() {
    local haproxy_template_file=${output_dir}/haproxy-template.cfg
    cp ${haproxy_config_file} ${haproxy_template_file}

    # configure a load balancer with static address
    local config_template_file=${output_dir}/haproxy-dynamic.cfg
    local static_config_file="${output_dir}/haproxy-static.cfg"

    update_in_file "${config_template_file}" \
      "{%backend.max.servers%}" "${HAPROXY_BACKEND_MAX_SERVERS}"
    configure_http_check "${config_template_file}"
    configure_http_check "${static_config_file}"

    cat ${config_template_file} >> ${haproxy_config_file}
    # This is used as the template to generate the configuration file
    # with dynamic list of servers
    cat "${static_config_file}" >> ${haproxy_template_file}
    cp ${haproxy_template_file} ${HAPROXY_CONFIG_DIR}/haproxy-template.cfg
}

configure_load_balancer() {
    if [ "${HAPROXY_CONFIG_MODE}" == "dns" ]; then
        configure_dns_backend
    elif [ "${HAPROXY_CONFIG_MODE}" == "static" ]; then
        configure_static_backend
    elif [ "${HAPROXY_CONFIG_MODE}" == "dynamic" ]; then
        configure_dynamic_backend
    else
        echo "WARNING: Unsupported configure mode: ${HAPROXY_CONFIG_MODE}"
    fi
}

configure_api_gateway() {
    # python script will use this template to generate config for API gateway backends
    cp ${haproxy_config_file} ${HAPROXY_CONFIG_DIR}/haproxy-template.cfg
}

configure_haproxy() {
    prepare_base_conf
    haproxy_config_file=${output_dir}/haproxy.cfg
    mkdir -p ${HAPROXY_HOME}/logs

    ETC_DEFAULT=/etc/default
    sudo mkdir -p ${ETC_DEFAULT}

    HAPROXY_CONFIG_DIR=${HAPROXY_HOME}/conf
    mkdir -p ${HAPROXY_CONFIG_DIR}

    update_in_file "${output_dir}/haproxy" \
      "{%haproxy.home%}" "${HAPROXY_HOME}"
    sudo cp ${output_dir}/haproxy ${ETC_DEFAULT}/haproxy

    # Fix the issue of haproxy service stop in docker (depending on --cap-add=SYS_PTRACE)
    # --exec flag of start-stop-daemon will use /proc/$pid/exe which for some unfathomable reason
    # needs the ptrace cap enabled or it fails under Docker.
    sudo sed -i \
      "s#--pidfile \"\$tmppid\" --exec \$HAPROXY#--pidfile \"\$tmppid\" --name \$BASENAME#g" \
      /etc/init.d/haproxy

    # TODO: to support user specified external IP address
    sed -i "s#{%frontend.ip%}#${NODE_IP_ADDRESS}#g" `grep "{%frontend.ip%}" -rl ${output_dir}`
    sed -i "s#{%frontend.port%}#${HAPROXY_FRONTEND_PORT}#g" `grep "{%frontend.port%}" -rl ${output_dir}`
    sed -i "s#{%frontend.protocol%}#${HAPROXY_FRONTEND_PROTOCOL}#g" `grep "{%frontend.protocol%}" -rl ${output_dir}`
    sed -i "s#{%backend.balance%}#${HAPROXY_BACKEND_BALANCE}#g" `grep "{%backend.balance%}" -rl ${output_dir}`

    if [ "${HAPROXY_APP_MODE}" == "load-balancer" ]; then
        configure_load_balancer
    elif [ "${NGINX_APP_MODE}" == "api-gateway" ]; then
        configure_api_gateway
    else
        echo "WARNING: Unknown application mode: ${NGINX_APP_MODE}"
    fi

    cp ${haproxy_config_file} ${HAPROXY_CONFIG_DIR}/haproxy.cfg
}

set_head_option "$@"
check_haproxy_installed
set_head_address
set_node_address
configure_haproxy

exit 0
