#!/bin/bash

# Current bin directory
BIN_DIR=`dirname "$0"`
ROOT_DIR="$(dirname "$(dirname "$BIN_DIR")")"

args=$(getopt -a -o h:: -l head:: -- "$@")
eval set -- "${args}"

USER_HOME=/home/$(whoami)
RUNTIME_PATH=$USER_HOME/runtime
MYSQL_HOME=$RUNTIME_PATH/mysql

# Util functions
. "$ROOT_DIR"/common/scripts/util-functions.sh

function prepare_base_conf() {
    local source_dir=$(dirname "${BIN_DIR}")/conf
    output_dir=/tmp/mysql/conf
    rm -rf  $output_dir
    mkdir -p $output_dir
    cp -r $source_dir/* $output_dir
}

function check_mysql_installed() {
    if ! command -v mysqld &> /dev/null
    then
        echo "MySQL is not installed for mysqld command is not available."
        exit 1
    fi
}

function update_data_dir() {
    local data_disk_dir=$(get_first_data_disk_dir)
    if [ -z "$data_disk_dir" ]; then
        data_dir="${MYSQL_HOME}/data"
    else
        data_dir="$data_disk_dir/mysql/data"
    fi

    mkdir -p ${data_dir}
    sed -i "s#{%data.dir%}#${data_dir}#g" ${config_template_file}
}

function update_server_id() {
    if [ ! -n "${CLOUDTIK_NODE_SEQ_ID}" ]; then
        echo "Replication needs unique server id. No node sequence id allocated for current node!"
        exit 1
    fi

    sed -i "s#{%server.id%}#${CLOUDTIK_NODE_SEQ_ID}#g" ${config_template_file}
}

function update_start_replication_on_boot() {
    if [ "${MYSQL_CLUSTER_MODE}" == "replication" ]; then
      if [ "${IS_HEAD_NODE}" != "true" ]; then
          sed -i "s#^skip_replica_start=ON#skip_replica_start=OFF#g" ${MYSQL_CONFIG_FILE}
      fi
    elif [ "${MYSQL_CLUSTER_MODE}" == "group_replication" ]; then
        sed -i "s#^group_replication_start_on_boot=OFF#group_replication_start_on_boot=ON#g" ${MYSQL_CONFIG_FILE}
    fi
}

function configure_mysql() {
    if [ "${IS_HEAD_NODE}" != "true" ] \
        && [ "${MYSQL_CLUSTER_MODE}" == "none" ]; then
          return
    fi

    prepare_base_conf

    if [ "${MYSQL_CLUSTER_MODE}" == "replication" ]; then
        config_template_file=${output_dir}/my-replication.cnf
    elif [ "${MYSQL_CLUSTER_MODE}" == "group_replication" ]; then
        config_template_file=${output_dir}/my-group-replication.cnf
    else
        config_template_file=${output_dir}/my.cnf
    fi

    mkdir -p ${MYSQL_HOME}/logs

    # ensure that /var/run/mysqld (used for socket and lock files) is writable
    # regardless of the UID our mysqld instance ends up having at runtime
    sudo mkdir -p /var/run/mysqld \
    && sudo chown -R $(whoami):$(id -gn) /var/run/mysqld \
    && sudo chmod 1777 /var/run/mysqld

    sed -i "s#{%bind.address%}#${NODE_IP_ADDRESS}#g" ${config_template_file}
    sed -i "s#{%bind.port%}#${MYSQL_SERVICE_PORT}#g" ${config_template_file}
    update_data_dir

    if [ "${MYSQL_CLUSTER_MODE}" == "replication" ]; then
        update_server_id
    elif [ "${MYSQL_CLUSTER_MODE}" == "group_replication" ]; then
        update_server_id
        sed -i "s#{%group.replication.group.name%}#${MYSQL_GROUP_REPLICATION_NAME}#g" ${config_template_file}
        # TODO: set head address as seed address is good for first start
        # But if head is dead while other workers are running, we need head start using workers as seeds
        # This need to be improved
        # While for workers, we can always trust there is a healthy head to contact with.
        sed -i "s#{%group.replication.seed.address%}#${HEAD_ADDRESS}#g" ${config_template_file}
    fi

    MYSQL_CONFIG_DIR=${MYSQL_HOME}/conf
    mkdir -p ${MYSQL_CONFIG_DIR}
    MYSQL_CONFIG_FILE=${MYSQL_CONFIG_DIR}/my.cnf
    cp -r ${config_template_file} ${MYSQL_CONFIG_FILE}

    # This is needed for mysql-init.sh to decide whether need to do user db setup
    MYSQL_MASTER_NODE=false
    if [ "${IS_HEAD_NODE}" == "true" ]; then
        MYSQL_MASTER_NODE=true
    else
        MYSQL_REPLICATION_SOURCE_HOST=${HEAD_ADDRESS}
    fi

    # check and initialize the database if needed
    bash $BIN_DIR/mysql-init.sh mysqld \
        --defaults-file=${MYSQL_CONFIG_FILE} >${MYSQL_HOME}/logs/mysql-init.log 2>&1

    # set the start replication on boot
    update_start_replication_on_boot
}

set_head_option "$@"
check_mysql_installed
set_node_ip_address
set_head_address
configure_mysql

exit 0
