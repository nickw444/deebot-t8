from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass, asdict

import click
from terminaltables import AsciiTable

from deebot_t8 import (
    ApiClient,
    DeebotEntity,
    PortalClient,
    DeebotAuthClient,
    SubscriptionClient,
    Credentials,
)
from deebot_t8.auth_client import Authenticator
from deebot_t8.entity import VacuumState
from deebot_t8.md5 import md5_hex
from .config import Config, load_config, write_config

LOGGER = logging.getLogger(__name__)


@dataclass
class TypedObj:
    config_path: str
    config: Config

    auth_client: DeebotAuthClient
    api_client: ApiClient | None = None
    subscription_client: SubscriptionClient | None = None

    entity: DeebotEntity | None = None


@click.group()
@click.pass_context
@click.option(
    "--config-file", type=click.Path(exists=False), default="deebot.conf.json"
)
def cli(ctx, config_file):
    logging.basicConfig()
    logging.getLogger("deebot_t8").setLevel(logging.DEBUG)

    config = load_config(config_file)

    def on_credentials_changed(credentials: Credentials):
        config.credentials = credentials
        write_config(config_file, config)

    auth_client = None
    api_client = None
    subscription_client = None

    if config is not None:
        portal_client = PortalClient(
            device_id=config.device_id,
            country=config.country,
            continent=config.continent,
        )
        auth_client = DeebotAuthClient(
            portal_client=portal_client,
            device_id=config.device_id,
            country=config.country,
        )
        authenticator = Authenticator(
            auth_client=auth_client,
            country=config.country,
            device_id=config.device_id,
            account_id=config.username,
            password_hash=config.password_hash,
            cached_credentials=config.credentials,
            on_credentials_changed=on_credentials_changed,
        )
        api_client = ApiClient(portal_client=portal_client, authenticator=authenticator)
        subscription_client = SubscriptionClient(
            authenticator=authenticator,
            continent=config.continent,
            device_id=config.device_id,
        )

    ctx.obj = TypedObj(
        config_path=config_file,
        config=config,
        auth_client=auth_client,
        api_client=api_client,
        subscription_client=subscription_client,
    )


def renew_access_tokens_impl(auth: DeebotAuthClient, config: Config):
    return auth.login(config.username, config.password_hash)


@cli.command()
@click.pass_obj
@click.option("--username", type=str, required=True)
@click.option("--password", type=str, required=True)
@click.option("--country", type=str, required=True)
@click.option("--continent", type=str, required=True)
@click.option("--regen-device", type=bool)
def login(obj: TypedObj, username, password, country, continent, regen_device):
    # TODO(NW): Infer continent from country

    if not regen_device and obj.config is not None and obj.config.device_id is not None:
        # Reuse existing device id if one exists
        device_id = obj.config.device_id
    else:
        device_id = md5_hex(str(time.time()))

    obj.config = Config(
        username=username,
        password_hash=md5_hex(password),
        device_id=device_id,
        country=country,
        continent=continent,
    )
    # Recreate clients for this special use case to apply new configuration
    # parameters (country, continent, device id)
    portal_client = PortalClient(
        device_id=obj.config.device_id,
        country=obj.config.country,
        continent=obj.config.continent,
    )
    auth_client = DeebotAuthClient(
        portal_client=portal_client,
        device_id=obj.config.device_id,
        country=obj.config.country,
    )

    write_config(obj.config_path, obj.config)
    obj.config.credentials = renew_access_tokens_impl(auth_client, obj.config)
    write_config(obj.config_path, obj.config)

    if obj.config.credentials is None:
        raise AssertionError

    click.echo(
        "Authenticated with user {}, token expires at {}".format(
            obj.config.credentials.user_id,
            obj.config.credentials.expires_at,
        )
    )


@cli.command()
@click.pass_obj
def renew_access_token(obj: TypedObj):
    obj.config.credentials = renew_access_tokens_impl(obj.auth_client, obj.config)
    write_config(obj.config_path, obj.config)

    if obj.config.credentials is None:
        raise AssertionError

    click.echo(
        "Renewed with user {}, token expires at {}".format(
            obj.config.credentials.user_id,
            obj.config.credentials.expires_at,
        )
    )


@cli.command()
@click.pass_obj
def list_devices(obj: TypedObj):
    if obj.api_client is None:
        raise AssertionError

    devices = obj.api_client.get_devices_list()
    table_data = [
        ["device id", "name", "product category", "model", "status"],
    ]
    for device in devices:
        table_data.append(
            [
                device.id,
                device.name,
                device.product_category,
                device.model_name,
                # status 0 seems to indicate offline?
                # status 1 online
                device.status,
            ]
        )
    print(AsciiTable(table_data).table)


@cli.group()
@click.pass_obj
@click.argument("device-name", type=str, required=False)
def device(obj: TypedObj, device_name):
    if obj.api_client is None or obj.subscription_client is None:
        raise AssertionError

    selected_device = None
    devices = obj.api_client.get_devices_list()
    for d in devices:
        if d.name == device_name:
            selected_device = d
            break
    else:
        raise click.UsageError(
            "Device with specified name ({}) could not be found.".format(device_name)
        )

    obj.entity = DeebotEntity(
        api_client=obj.api_client,
        subs_client=obj.subscription_client,
        device=selected_device,
    )


@device.command()
@click.pass_obj
def subscribe(obj: TypedObj):
    # Silence the logger to allow our table to display nicely
    logging.getLogger("deebot_t8").setLevel(logging.ERROR)
    if obj.entity is None:
        raise AssertionError

    def on_state_change(state: VacuumState, attribute: str):
        click.clear()
        table_data = [["Attribute", "Value"]]
        for attr, value in asdict(state).items():
            if isinstance(value, list):
                for entry in value:
                    table_data.append([attr, entry])
            else:
                table_data.append([attr, value])
        print(AsciiTable(table_data).table)

    obj.entity.subscribe(on_state_change)

    def handler(signum, frame):
        obj.entity.unsubscribe(on_state_change)
        raise KeyboardInterrupt

    # Wait forever (until SIGINT at least)
    signal.signal(signal.SIGINT, handler)
    signal.pause()


@device.command()
@click.pass_obj
def clean(obj: TypedObj):
    if obj.entity is None:
        raise AssertionError

    obj.entity.clean()


@device.command()
@click.pass_obj
@click.argument("areas", type=int, nargs=-1, required=True)
def clean_areas(obj: TypedObj, areas):
    if obj.entity is None:
        raise AssertionError

    obj.entity.clean_areas(areas)


@device.command()
@click.pass_obj
@click.argument("custom", type=str)
def clean_custom(obj: TypedObj, custom):
    if obj.entity is None:
        raise AssertionError

    obj.entity.clean_custom(custom)


@device.command()
@click.pass_obj
def stop(obj: TypedObj):
    if obj.entity is None:
        raise AssertionError

    obj.entity.stop()


@device.command()
@click.pass_obj
def pause(obj: TypedObj):
    if obj.entity is None:
        raise AssertionError

    obj.entity.pause()


@device.command()
@click.pass_obj
def return_to_charge(obj: TypedObj):
    if obj.entity is None:
        raise AssertionError

    obj.entity.return_to_charge()


@device.command()
@click.pass_obj
def relocate(obj: TypedObj):
    if obj.entity is None:
        raise AssertionError

    obj.entity.relocate()


@device.command()
@click.pass_obj
def play_sound(obj: TypedObj):
    if obj.entity is None:
        raise AssertionError

    obj.entity.play_sound()


@device.command()
@click.pass_obj
@click.argument("enable", type=bool)
def set_true_detect(obj: TypedObj, enable: bool):
    if obj.entity is None:
        raise AssertionError

    obj.entity.set_true_detect(enable)


@device.command()
@click.pass_obj
@click.argument("enable", type=bool)
def set_clean_preference(obj: TypedObj, enable: bool):
    if obj.entity is None:
        raise AssertionError

    obj.entity.set_clean_preference(enable)


@device.command()
@click.pass_obj
def set_water_level(obj: TypedObj):
    pass


@device.command()
@click.pass_obj
def set_vacuum_speed(obj: TypedObj):
    pass


@device.command()
@click.pass_obj
def send_command(obj: TypedObj):
    pass


if __name__ == "__main__":
    cli()
