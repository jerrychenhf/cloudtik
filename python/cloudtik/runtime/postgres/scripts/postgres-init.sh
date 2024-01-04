#!/usr/bin/env bash

# Current bin directory
BIN_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Load postgres functions
. "$BIN_DIR"/postgres.sh
. "$BIN_DIR"/repmgr.sh

# check to see if this file is being run or sourced from another script
_is_sourced() {
	# https://unix.stackexchange.com/a/215279
	[ "${#FUNCNAME[@]}" -ge 2 ] \
		&& [ "${FUNCNAME[0]}" = '_is_sourced' ] \
		&& [ "${FUNCNAME[1]}" = 'source' ]
}

_main() {
	# if first arg looks like a flag, assume we want to run postgres server
	if [ "${1:0:1}" = '-' ]; then
		set -- postgres "$@"
	fi

	if [ "$1" = 'postgres' ] && ! _pg_want_help "$@"; then
		postgres_setup_env
		# setup data directories and permissions (when run as root)
		postgres_create_db_directories
		if [ "$(id -u)" = '0' ]; then
			# then restart script as postgres user
			exec gosu postgres "$BASH_SOURCE" "$@"
		fi

		# only run initialization on an empty data directory
		if [ -z "$DATABASE_ALREADY_EXISTS" ]; then
			postgres_verify_minimum_env

			if [ ! -z "${POSTGRES_INITDB_SCRIPTS}" ]; then
			  # check dir permissions to reduce likelihood of half-initialized database
			  ls ${POSTGRES_INITDB_SCRIPTS}/ > /dev/null
			fi

			if [ "${POSTGRES_MASTER_NODE}" == "true" ]; then
				postgres_init_database_dir
				pg_setup_hba_conf "$@"
				if [ "${POSTGRES_REPMGR_ENABLED}" == "true" ]; then
					repmgr_setup_hba_conf "$@"
				fi

				# PGPASSWORD is required for psql when authentication is required for 'local' connections via pg_hba.conf and is otherwise harmless
				# e.g. when '--auth=md5' or '--auth-local=md5' is used in POSTGRES_INITDB_ARGS
				export PGPASSWORD="${PGPASSWORD:-$POSTGRES_PASSWORD}"
				postgres_temp_server_start "$@"

				postgres_setup_db
				postgres_setup_replication_user

				if [ "${POSTGRES_REPMGR_ENABLED}" == "true" ]; then
					repmgr_create_repmgr_user
					repmgr_create_repmgr_db
				fi

				postgres_init_db_and_user
				if [ ! -z "${POSTGRES_INITDB_SCRIPTS}" ]; then
				  postgres_process_init_files ${POSTGRES_INITDB_SCRIPTS}/*
				fi

				postgres_temp_server_stop
				unset PGPASSWORD
			else
				# for replica, we needs to do a pg_basebackup from master
				# Cannot use an emtpy data directory or a data directory initialized
				# by initdb (this method will make the data files with different identifier.
				# This process will setup primary_conninfo in the postgres.auto.conf
				# and the standby.signal in the data directory
				export PGPASSWORD="${POSTGRES_REPLICATION_PASSWORD:-cloudtik}"
				local replication_slot_options=""
				if [ ! -z "$POSTGRES_REPLICATION_SLOT_NAME" ]; then
					replication_slot_options="-C -S $POSTGRES_REPLICATION_SLOT_NAME"
				fi
				pg_basebackup -h ${POSTGRES_PRIMARY_HOST} \
					-U repl_user --no-password ${replication_slot_options} \
					-X stream -R -D $PGDATA
				unset PGPASSWORD
			fi

			cat <<-'EOM'

				PostgreSQL init process complete; ready for start up.

			EOM
		else
			cat <<-'EOM'

				PostgreSQL Database directory appears to contain a database; Skipping initialization

			EOM
		fi
		if [ "${POSTGRES_REPMGR_ENABLED}" == "true" ]; then
			repmgr_configure_preload
		if
		postgres_setup_synchronous_standby
	fi

  #  Use this as init script
	# exec "$@"
}

if ! _is_sourced; then
	_main "$@"
fi
