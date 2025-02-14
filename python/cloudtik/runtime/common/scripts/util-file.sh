#!/bin/bash
# Copyright VMware, Inc.
# SPDX-License-Identifier: APACHE-2.0
#
# Library for managing files

# shellcheck disable=SC1091

# Functions

########################
# Replace a regex-matching string in a file
# Arguments:
#   $1 - filename
#   $2 - match regex
#   $3 - substitute regex
#   $4 - use POSIX regex. Default: true
# Returns:
#   None
#########################
replace_in_file() {
    local filename="${1:?filename is required}"
    local match_regex="${2:?match regex is required}"
    local substitute_regex="${3:?substitute regex is required}"
    local posix_regex=${4:-true}

    local result

    # We should avoid using 'sed in-place' substitutions
    # 1) They are not compatible with files mounted from ConfigMap(s)
    # 2) We found incompatibility issues with Debian10 and "in-place" substitutions
    local -r del=$'\001' # Use a non-printable character as a 'sed' delimiter to avoid issues
    if [[ $posix_regex = true ]]; then
        result="$(sed -E "s${del}${match_regex}${del}${substitute_regex}${del}g" "$filename")"
    else
        result="$(sed "s${del}${match_regex}${del}${substitute_regex}${del}g" "$filename")"
    fi
    echo "$result" > "$filename"
}

########################
# Remove a line in a file based on a regex
# Arguments:
#   $1 - filename
#   $2 - match regex
#   $3 - use POSIX regex. Default: true
# Returns:
#   None
#########################
remove_in_file() {
    local filename="${1:?filename is required}"
    local match_regex="${2:?match regex is required}"
    local posix_regex=${3:-true}
    local result

    # We should avoid using 'sed in-place' substitutions
    # 1) They are not compatible with files mounted from ConfigMap(s)
    # 2) We found incompatibility issues with Debian10 and "in-place" substitutions
    if [[ $posix_regex = true ]]; then
        result="$(sed -E "/$match_regex/d" "$filename")"
    else
        result="$(sed "/$match_regex/d" "$filename")"
    fi
    echo "$result" > "$filename"
}

########################
# Appends text after the last line matching a pattern
# Arguments:
#   $1 - file
#   $2 - match regex
#   $3 - contents to add
# Returns:
#   None
#########################
append_file_after_last_match() {
    local file="${1:?missing file}"
    local match_regex="${2:?missing pattern}"
    local value="${3:?missing value}"

    # We read the file in reverse, replace the first match (0,/pattern/s) and then reverse the results again
    result="$(tac "$file" | sed -E "0,/($match_regex)/s||${value}\n\1|" | tac)"
    echo "$result" > "$file"
}

########################
# Replace a regex-matching string in a file in-place
# Arguments:
#   $1 - filename
#   $2 - match regex
#   $3 - substitute regex
# Returns:
#   None
#########################
update_in_file() {
    local filename="${1:?filename is required}"
    local match_regex="${2:?match regex is required}"
    local substitute_regex="${3:-}"
    local -r del=$'\001' # Use a non-printable character as a 'sed' delimiter to avoid issues
    sed -i "s${del}${match_regex}${del}${substitute_regex}${del}g" "$filename"
}

########################
# Set the variable export in file
# Arguments:
#   $1 - filename
#   $2 - variable name
#   $3 - variable value
# Returns:
#   None
#########################
set_variable_in_file() {
    local filename="${1:?filename is required}"
    local -r variable_name="${2:?variable name is required}"
    local -r variable_value="${3:-}"

    if grep -qE "^export ${variable_name}=" "$filename" >/dev/null; then
        replace_in_file "$filename" \
          "^export ${property}=.*" "export ${variable_name}=\"${variable_value}\"" false
    else
        echo "export ${variable_name}=\"${variable_value}\"" \
          >>"$filename"
    fi
}
