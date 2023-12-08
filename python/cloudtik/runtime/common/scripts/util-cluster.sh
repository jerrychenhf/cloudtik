#!/bin/bash

set_head_address() {
    set_head_ip_address
    # TODO: set the head host address when applicable
}

set_head_ip_address() {
    if [ -z "${HEAD_IP_ADDRESS}" ]; then
        if [ $IS_HEAD_NODE == "true" ]; then
            if [ ! -n "${CLOUDTIK_NODE_IP}" ]; then
                HEAD_IP_ADDRESS=$(hostname -I | awk '{print $1}')
            else
                HEAD_IP_ADDRESS=${CLOUDTIK_NODE_IP}
            fi
        else
            if [ ! -n "${CLOUDTIK_HEAD_IP}" ]; then
                echo "Error: CLOUDTIK_HEAD_IP environment variable should be set."
                exit 1
            else
                HEAD_IP_ADDRESS=${CLOUDTIK_HEAD_IP}
            fi
        fi
    fi
}

set_node_address() {
    set_node_ip_address
    # TODO: set the node host address when applicable
}

set_node_ip_address() {
    if [ -z "${NODE_IP_ADDRESS}" ]; then
        if [ ! -n "${CLOUDTIK_NODE_IP}" ]; then
            NODE_IP_ADDRESS=$(hostname -I | awk '{print $1}')
        else
            NODE_IP_ADDRESS=${CLOUDTIK_NODE_IP}
        fi
    fi
}

set_head_option() {
    # this function set the head variable based on the arguments processed by getopt
    IS_HEAD_NODE=false
    while true
    do
        case "$1" in
        -h|--head)
            IS_HEAD_NODE=true
            ;;
        --)
            shift
            break
            ;;
        esac
        shift
    done
}

set_service_command() {
    # this function set the SERVICE_COMMAND
    # based on the arguments processed by getopt
    while true
    do
        case "$1" in
        --)
            shift
            break
            ;;
        esac
        shift
    done
    SERVICE_COMMAND="$1"
}

get_data_disk_dirs() {
    local data_disk_dirs=""
    if [ -d "/mnt/cloudtik" ]; then
        for data_disk in /mnt/cloudtik/*; do
            [ -d "$data_disk" ] || continue
            if [ -z "$data_disk_dirs" ]; then
                data_disk_dirs=$data_disk
            else
                data_disk_dirs="$data_disk_dirs,$data_disk"
            fi
        done
    fi
    echo "${data_disk_dirs}"
}

get_first_data_disk_dir() {
    local data_disk_dir=""
    if [ -d "/mnt/cloudtik" ]; then
        for data_disk in /mnt/cloudtik/*; do
            [ -d "$data_disk" ] || continue
            data_disk_dir=$data_disk
            break
        done
    fi
    echo "${data_disk_dir}"
}

########################
# Get a comma separated list of data disk paths with named data dir
# Arguments:
#   $1 - sub_dir
#   $2 - make_dir: default false
# Returns:
#   Comma separated list of data disk paths
#########################
get_data_disk_dirs_of() {
    local sub_dir="${1:?Sub directory is required}"
    local make_dir=${2:-false}
    local data_disk_dirs=""
    if [ -d "/mnt/cloudtik" ]; then
        for data_disk in /mnt/cloudtik/*; do
            [ -d "$data_disk" ] || continue
            local data_dir="$data_disk/$sub_dir"
            if [[ $make_dir = true ]]; then
              mkdir -p "$data_dir"
            fi
            if [ -z "$data_disk_dirs" ]; then
                data_disk_dirs="$data_dir"
            else
                data_disk_dirs="$data_disk_dirs,$data_dir"
            fi
        done
    fi
    echo "${data_disk_dirs}"
}
