import json
import os
from typing import Any, Dict, Optional

from cloudtik.core._private.util.core_utils import get_cloudtik_temp_dir, get_json_object_hash, open_with_mode
from cloudtik.core._private.debug import log_once
from cloudtik.core._private.utils import prepare_config, decrypt_config, runtime_prepare_config, validate_config, \
    verify_config, encrypt_config, RUNTIME_CONFIG_KEY, runtime_bootstrap_config, load_yaml_config
from cloudtik.core._private.provider_factory import _NODE_PROVIDERS, _PROVIDER_PRETTY_NAMES
from cloudtik.core._private.cli_logger import cli_logger, cf

CONFIG_CACHE_VERSION = 1


def try_logging_config(config: Dict[str, Any]) -> None:
    if config["provider"]["type"] == "aws":
        from cloudtik.providers._private.aws.config import log_to_cli
        log_to_cli(config)


def try_get_log_state(provider_config: Dict[str, Any]) -> Optional[dict]:
    if provider_config["type"] == "aws":
        from cloudtik.providers._private.aws.config import get_log_state
        return get_log_state()
    return None


def try_reload_log_state(provider_config: Dict[str, Any],
                         log_state: dict) -> None:
    if not log_state:
        return
    if provider_config["type"] == "aws":
        from cloudtik.providers._private.aws.config import reload_log_state
        return reload_log_state(log_state)


def _bootstrap_config(
        config: Dict[str, Any],
        no_config_cache: bool = False,
        init_config_cache: bool = False,
        skip_runtime_bootstrap: bool = False) -> Dict[str, Any]:
    # Check if bootstrapped, return if it is the case
    if config.get("bootstrapped", False):
        return config

    config = prepare_config(config)
    # NOTE: multi-node-type cluster scaler is guaranteed to be in use after this.

    config_hash = get_json_object_hash([config])
    config_cache_dir = os.path.join(get_cloudtik_temp_dir(), "configs")
    cache_key = os.path.join(
        config_cache_dir,
        "cloudtik-config-{}".format(config_hash))

    if os.path.exists(cache_key) and not no_config_cache:
        with open(cache_key) as f:
            config_cache = json.loads(f.read())
        if config_cache.get("_version", -1) == CONFIG_CACHE_VERSION:
            # todo: is it fine to re-resolve? afaik it should be.
            # we can have migrations otherwise or something
            # but this seems overcomplicated given that resolving is
            # relatively cheap
            cached_config = decrypt_config(config_cache["config"])
            try_reload_log_state(
                cached_config["provider"],
                config_cache.get("provider_log_info"))

            if log_once("_printed_cached_config_warning"):
                cli_logger.verbose_warning(
                    "Loaded cached provider configuration "
                    "from " + cf.bold("{}"), cache_key)
                cli_logger.verbose_warning(
                    "If you experience issues with "
                    "the cloud provider, try re-running "
                    "the command with {}.", cf.bold("--no-config-cache"))

            return cached_config
        else:
            cli_logger.warning(
                "Found cached cluster config "
                "but the version " + cf.bold("{}") + " "
                "(expected " + cf.bold("{}") + ") does not match.\n"
                "This is normal if cluster launcher was updated.\n"
                "Config will be re-resolved.",
                config_cache.get("_version", "none"), CONFIG_CACHE_VERSION)

    importer = _NODE_PROVIDERS.get(config["provider"]["type"])
    if not importer:
        raise NotImplementedError("Unsupported provider {}".format(
            config["provider"]))

    provider_cls = importer(config["provider"])

    cli_logger.print(
        "Checking {} environment settings",
        _PROVIDER_PRETTY_NAMES.get(config["provider"]["type"]))

    config = provider_cls.post_prepare(config)

    if not skip_runtime_bootstrap:
        config = runtime_prepare_config(
            config.get(RUNTIME_CONFIG_KEY), config)

    try:
        validate_config(
            config, skip_runtime_validate=skip_runtime_bootstrap)
    except (ModuleNotFoundError, ImportError):
        cli_logger.abort(
            "Not all dependencies were found. Please "
            "update your install command.")

    resolved_config = provider_cls.bootstrap_config(config)

    if not skip_runtime_bootstrap:
        # final round to runtime for config prepare
        resolved_config = runtime_bootstrap_config(
            config.get(RUNTIME_CONFIG_KEY), resolved_config)

    # add a verify step
    verify_config(
        resolved_config, skip_runtime_verify=skip_runtime_bootstrap)

    if not no_config_cache or init_config_cache:
        os.makedirs(config_cache_dir, exist_ok=True)
        with open_with_mode(cache_key, "w", os_mode=0o600) as f:
            encrypted_config = encrypt_config(resolved_config)
            config_cache = {
                "_version": CONFIG_CACHE_VERSION,
                "provider_log_info": try_get_log_state(
                    resolved_config["provider"]),
                "config": encrypted_config
            }
            f.write(json.dumps(config_cache))
    return resolved_config


def _load_cluster_config(
        config_file: str,
        override_cluster_name: Optional[str] = None,
        should_bootstrap: bool = True,
        no_config_cache: bool = False,
        skip_runtime_bootstrap: bool = False) -> Dict[str, Any]:
    config = load_yaml_config(config_file)
    if override_cluster_name is not None:
        config["cluster_name"] = override_cluster_name
    if should_bootstrap:
        config = _bootstrap_config(
            config, no_config_cache=no_config_cache,
            skip_runtime_bootstrap=skip_runtime_bootstrap)
    return config
