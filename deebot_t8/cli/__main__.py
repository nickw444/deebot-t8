import logging
import threading
import time
from dataclasses import dataclass

import click
from terminaltables import AsciiTable

from deebot_t8 import (
    ApiClient, DeebotEntity, PortalClient, DeebotAuthClient,
    SubscriptionClient, VacInfo)
from deebot_t8.md5 import md5_hex
from .config import Config, load_config, write_config

LOGGER = logging.getLogger(__name__)


@dataclass
class TypedObj:
    config_path: str
    config: Config

    auth_client: DeebotAuthClient
    api_client: ApiClient = None
    subscription_client: SubscriptionClient = None

    entity: DeebotEntity = None


@click.group()
@click.pass_context
@click.option('--config-file', type=click.Path(exists=False),
              default='deebot.conf.json')
def cli(ctx, config_file):
    logging.basicConfig()
    logging.getLogger('deebot_t8').setLevel(logging.DEBUG)

    config = load_config(config_file)

    auth_client = None
    api_client = None
    subscription_client = None

    if config is not None:
        portal_client = PortalClient(config.device_id, config.country,
                                     config.continent)
        auth_client = DeebotAuthClient(portal_client, config.device_id,
                                       config.country, config.continent)
        api_client = ApiClient(portal_client=portal_client)
        subscription_client = SubscriptionClient(
            country='au',
            continent='eu',
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
@click.option('--username', type=str, required=True)
@click.option('--password', type=str, required=True)
@click.option('--country', type=str, required=True)
@click.option('--continent', type=str, required=True)
def login(obj: TypedObj, username, password, country, continent):
    # TODO(NW): Infer continent from country
    obj.config = Config(
        username=username,
        password_hash=md5_hex(password),
        device_id=md5_hex(str(time.time())),
        country=country,
        continent=continent,
    )
    write_config(obj.config_path, obj.config)
    obj.config.credentials = renew_access_tokens_impl(obj.auth_client,
                                                      obj.config)
    write_config(obj.config_path, obj.config)


@cli.command()
@click.pass_obj
def renew_access_tokens(obj: TypedObj):
    obj.config.credentials = renew_access_tokens_impl(obj.auth_client,
                                                      obj.config)
    write_config(obj.config_path, obj.config)


@cli.command()
@click.pass_obj
def list_devices(obj: TypedObj):
    devices = obj.api_client.get_devices_list(obj.config.credentials)
    table_data = [
        ['device id', 'name', 'product category', 'model', 'status'],
    ]
    for device in devices:
        table_data.append([
            device['did'],
            device['nick'],
            device['product_category'],
            device['model'],
            # status 0 seems to indicate offline?
            # status 1 online
            device['status']
        ])
    print(AsciiTable(table_data).table)


@cli.group()
@click.pass_obj
@click.argument('device-name', type=str, required=False)
def device(obj: TypedObj, device_name):
    selected_device = None
    devices = obj.api_client.get_devices_list(obj.config.credentials)
    for d in devices:
        if d['nick'] == device_name:
            selected_device = d
            break
    else:
        raise click.UsageError(
            'Device with specified name ({}) could not be found.'.format(
                device_name))

    vacinfo = VacInfo(
        id=selected_device['did'],
        resource=selected_device['resource'],
        cls=selected_device['class'],
    )
    obj.entity = DeebotEntity(obj.api_client, obj.subscription_client,
                              obj.config.credentials, vacinfo)


@device.command()
@click.pass_obj
def subscribe(obj: TypedObj):
    def poll():
        while True:
            obj.entity.force_refresh()
            time.sleep(15)

    threading.Thread(target=poll).start()
    obj.subscription_client.connect(threaded=False,
                                    credentials=obj.config.credentials)


@device.command()
@click.pass_obj
def clean(obj: TypedObj):
    obj.entity.clean()


@device.command()
@click.pass_obj
@click.argument('areas', type=int, nargs=-1, required=True)
def clean_areas(obj: TypedObj, areas):
    obj.entity.clean_areas(areas)


@device.command()
@click.pass_obj
@click.argument('custom', type=str)
def clean_custom(obj: TypedObj, custom):
    obj.entity.clean_custom(custom)


@device.command()
@click.pass_obj
def stop(obj: TypedObj):
    obj.entity.stop()


@device.command()
@click.pass_obj
def pause(obj: TypedObj):
    obj.entity.pause()


@device.command()
@click.pass_obj
def return_to_charge(obj: TypedObj):
    obj.entity.return_to_charge()


@device.command()
@click.pass_obj
def relocate(obj: TypedObj):
    obj.entity.relocate()


@device.command()
@click.pass_obj
def play_sound(obj: TypedObj):
    obj.entity.play_sound()


@device.command()
@click.pass_obj
@click.argument('enable', type=bool)
def set_true_detect(obj: TypedObj, enable: bool):
    obj.entity.set_true_detect(enable)


@device.command()
@click.pass_obj
@click.argument('enable', type=bool)
def set_clean_preference(obj: TypedObj, enable: bool):
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


if __name__ == '__main__':
    cli()
