import json
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Callable, Set

from deebot_t8.api_client import ApiClient, DeviceInfo
from deebot_t8.exceptions import ApiErrorException
from deebot_t8.subscription_client import SubscriptionClient

LOGGER = logging.getLogger(__name__)


@dataclass
class ComponentLifeSpan:
    component: str
    total: int
    left: int


@dataclass
class TotalStats:
    area: int
    time: int
    count: int


@dataclass
class CleanStats:
    area: int
    time: int
    avoid_count: int
    start_time: int


@dataclass
class VacuumState:
    class Speed(Enum):
        QUIET = 1
        STANDARD = 2
        MAX = 3
        MAX_PLUS = 4

    class WaterFlow(Enum):
        LOW = 1
        MEDIUM = 2
        HIGH = 3
        ULTRA_HIGH = 4

    class RobotState(Enum):
        IDLE = 1
        CLEANING = 2
        PAUSED = 3
        RETURNING = 4

    class CleanType(Enum):
        AUTO = 1
        SPOT_AREA = 2
        CUSTOM_AREA = 3

    is_online: bool = None
    firmware_version: str = None
    hardware_version: str = None

    state: RobotState = None
    clean_type: CleanType = None
    clean_stats: CleanStats = None

    battery_level: int = None
    is_charging: bool = None

    mop_attached: bool = None
    water_level: WaterFlow = None

    vacuum_speed: Speed = None
    clean_count: int = None

    cleaning_preference_enabled: bool = None
    true_detect_enabled: bool = None
    auto_boost_suction_enabled: bool = None
    auto_empty_enabled: bool = None

    lifespan: List[ComponentLifeSpan] = None
    total_stats: TotalStats = None

    _on_change = None

    def __setattr__(self, key, value):
        super(VacuumState, self).__setattr__(key, value)
        if getattr(self, key) != value:
            LOGGER.debug("set state: {} -> {}".format(key, value))

        if self._on_change is not None:
            self._on_change(self, key)


class DeebotEntity:
    def __init__(self, api_client: ApiClient, subs_client: SubscriptionClient, device: DeviceInfo):
        self._api_client = api_client
        self._subs_client = subs_client
        self._device = device

        self._lock = threading.Lock()
        self._subscribers: Set[Callable[[VacuumState, str], None]] = set()

        self.state: VacuumState = VacuumState(is_online=device.status == 1)
        self.state._on_change = self._handle_state_change

        self._should_poll = False
        self._err_count = 0

    def force_refresh(self):
        requests = [
            ('getInfo', [
                "getCleanInfo_V2",
                "getWaterInfo",
                "getChargeState",
                "getBattery",
                "getStats",
                "getError",
            ]),
            # Make an additional getInfo call to avoid response payload size
            # restriction. It appears larger response payloads are returned via
            # MQTT across multiple messages. This saves additional
            # implementation complexity to support that.
            ('getInfo', [
                "getTotalStats",
                "getSpeed",
                "getCleanCount",
                "getTrueDetect",
                'getCleanPreference',
                "getAutoEmpty",
                "getCarpertPressure", # <- Yes there is actually a typo in the api 🤷‍
            ]),
            ('getLifeSpan', [
                "sideBrush",
                "brush",
                "heap",
                "unitCare"
            ]),
            # The following requests are unused as they have an unknown purpose:
            # ("getBlock", None),
            # ("getBreakPoint", None),
        ]

        for (command, params) in requests:
            resp = self.exc_command(command, params)
            self.handle_command(command, resp['body'], resp['header'])

    def handle_mqtt_message(self, command, body, log=True):
        if log:
            LOGGER.debug("mq: {} {}".format(command, body))

        data = body['data']
        if command == "onBattery":
            self.state.battery_level = data['value']
        elif command == 'onChargeState':
            self.state.is_charging = bool(data['isCharging'])
        elif command == 'onCleanCount':
            self.state.clean_count = data['count']
        elif command == 'onCleanInfo_V2':
            if data['state'] == 'clean':
                clean_state = data['cleanState']
                motion_state = clean_state['motionState']
                clean_type = clean_state['content']['type']

                if motion_state == 'working':
                    self.state.state = VacuumState.RobotState.CLEANING
                elif motion_state == 'pause':
                    self.state.state = VacuumState.RobotState.PAUSED
                else:
                    LOGGER.warning(
                        "Unhandled motion state: {}".format(motion_state))

                if clean_type == 'auto':
                    self.state.clean_type = VacuumState.CleanType.AUTO
                elif clean_type == 'spotArea':
                    self.state.clean_type = VacuumState.CleanType.SPOT_AREA
                elif clean_type == 'customArea':
                    self.state.clean_type = VacuumState.CleanType.CUSTOM_AREA
                else:
                    LOGGER.warning(
                        "Unhandled clean type: {}".format(clean_type))

            elif data['state'] == 'idle':
                self.state.state = VacuumState.RobotState.IDLE
                self.state.clean_type = None
            elif data['state'] == 'goCharging':
                self.state.state = VacuumState.RobotState.RETURNING
                self.state.clean_type = None
        elif command == 'onCleanPreference':
            self.state.cleaning_preference_enabled = bool(data['enable'])
        elif command == 'onFwBuryPoint':
            cmd_payload = json.loads(data['content'])
            cmd_rn = cmd_payload['rn']

            if cmd_rn == 'bd_setting':
                cmd_data_raw = cmd_payload['d']['body']['data']['d_val']
                cmd_data = json.loads(cmd_data_raw)
                # Set normalized water level, water levels reported via
                # onFwBuryPoint::bd_setting are indexed from 0 through 3
                water_flow_map = {
                    0: VacuumState.WaterFlow.LOW,
                    1: VacuumState.WaterFlow.MEDIUM,
                    2: VacuumState.WaterFlow.HIGH,
                    3: VacuumState.WaterFlow.ULTRA_HIGH,
                }
                self.state.water_level = water_flow_map[
                    cmd_data['waterAmount']]
                self.state.auto_boost_suction_enabled = bool(
                    cmd_data['isPressurized'])
            else:
                LOGGER.debug(
                    "Unhandled onFwBuryPoint message: {} {}".format(cmd_rn,
                                                                    cmd_payload))
        elif command == 'onSpeed':
            speed_map = {
                1000: VacuumState.Speed.QUIET,
                0: VacuumState.Speed.STANDARD,
                1: VacuumState.Speed.MAX,
                2: VacuumState.Speed.MAX_PLUS,
            }
            self.state.vacuum_speed = speed_map[data['speed']]
        elif command == 'onStats':
            # mq: onStats {'data': {'area': 0, 'time': 0, 'cid': '111', 'start': '1624877148', 'type': 'customArea', 'enablePowerMop': 0, 'powerMopType': 1, 'aiopen': 1, 'aitypes': [], 'avoidCount': 0}}
            # mq: onStats {'data': {'area': 0, 'time': 0, 'cid': '111', 'start': '1624877304', 'type': 'spotArea', 'enablePowerMop': 0, 'powerMopType': 1, 'aiopen': 1, 'aitypes': [], 'avoidCount': 0}}
            # mq: onStats {'data': {'area': 0, 'time': 0, 'cid': '111', 'start': '1624877619', 'type': 'auto', 'enablePowerMop': 0, 'powerMopType': 1, 'aiopen': 1, 'aitypes': [], 'avoidCount': 0}}
            # mq: onStats {'data': {'area': 0, 'time': 0, 'cid': '111', 'start': '1624877619', 'type': 'auto', 'enablePowerMop': 0, 'powerMopType': 1, 'aiopen': 1, 'aitypes': [], 'avoidCount': 0}}
            # mq: onStats {'data': {'area': 0, 'time': 61, 'cid': '111', 'start': '1624877304', 'type': 'spotArea', 'enablePowerMop': 0, 'powerMopType': 1, 'aiopen': 1, 'aitypes': [], 'avoidCount': 0}}
            # TODO(NW): Handle mopping info in this message (enablePowerMop, powerMopType)
            self.state.clean_stats = CleanStats(
                area=data['area'],
                time=data['time'],
                avoid_count=data['avoidCount'],
                start_time=data['start'],
            )

            # TODO(NW): Determine whether clean type should be inferred from this?
            #             - does it introduce a race?
            typ = data['type']
            if typ == 'auto':
                self.state.clean_type = VacuumState.CleanType.AUTO
            elif typ == 'spotArea':
                self.state.clean_type = VacuumState.CleanType.SPOT_AREA
            elif typ == 'customArea':
                self.state.clean_type = VacuumState.CleanType.CUSTOM_AREA
            else:
                LOGGER.warning("Unknown clean type: {}".format(typ))
        elif command == 'onTrueDetect':
            self.state.true_detect_enabled = bool(data['enable'])
        elif command == 'onWaterInfo':
            self.state.mop_attached = bool(data['enable'])
            # Set normalized water level, water levels reported via onWaterInfo
            # are indexed from 1 through 4
            water_flow_map = {
                1: VacuumState.WaterFlow.LOW,
                2: VacuumState.WaterFlow.MEDIUM,
                3: VacuumState.WaterFlow.HIGH,
                4: VacuumState.WaterFlow.ULTRA_HIGH,
            }
            self.state.water_level = water_flow_map[data['amount']]
        elif command == 'reportStats':
            # mq: reportStats {'data': {'cid': '1117230632', 'stop': 0, 'enablePowerMop': 0, 'powerMopType': 2, 'stopReason': 1, 'startReason': 1, 'type': 'spotArea'}}
            # mq: reportStats {'data': {'cid': '1117230632', 'stop': 1, 'enablePowerMop': 0, 'powerMopType': 1, 'stopReason': 2, 'startReason': 1, 'type': 'spotArea', 'mapCount': 9, 'area': 0, 'start': '1624877304', 'time': 61, 'content': '1', 'aiopen': 1, 'aitypes': [], 'aiavoid': 0}}
            # mq: reportStats {'data': {'cid': '2132509283', 'stop': 0, 'enablePowerMop': 0, 'powerMopType': 2, 'stopReason': 1, 'startReason': 1, 'type': 'customArea'}}
            # mq: reportStats {'data': {'cid': '2132509283', 'stop': 1, 'enablePowerMop': 0, 'powerMopType': 1, 'stopReason': 1, 'startReason': 1, 'type': 'customArea', 'mapCount': 9, 'area': 0, 'start': '1624877148', 'time': 0, 'content': '-2382.000000,-563.000000,-1998.000000,-1323.000000', 'aiopen': 1, 'aitypes': [], 'aiavoid': 0}}
            # mq: reportStats {'data': {'cid': '2147037274', 'stop': 0, 'enablePowerMop': 0, 'powerMopType': 2, 'stopReason': 1, 'startReason': 1, 'type': 'spotArea'}}
            # mq: reportStats {'data': {'cid': '67289670', 'stop': 0, 'enablePowerMop': 0, 'powerMopType': 2, 'stopReason': 2, 'startReason': 1, 'type': 'auto'}}
            pass
        elif command == 'onBreakPointStatus':
            pass
        elif command == 'onCachedMapInfo':
            pass
        elif command in (
                'reportMinorMap', 'reportPos', 'reportMapTrace',
                'reportMapSubSet'):
            pass
        elif command == 'onError':
            pass
        elif command == 'onEvt':
            pass
        elif command == 'onMajorMap':
            pass
        elif command == 'onMapSet':
            pass
        elif command == 'onMapState':
            pass
        elif command == 'onMapTrace':
            pass
        elif command == 'onMinorMap':
            pass
        elif command == 'onPos':
            pass
        elif command == 'onRelocationState':
            pass
        elif command == 'onRosNodeReady':
            pass
        elif command == 'onSched_V2':
            pass
        else:
            LOGGER.warning(
                "Unhandled mqtt command: {} {}".format(command, data))

    def exc_command(self, command, data=None):
        try:
            rv = self._api_client.exc_command(self._device, command, data)
        except ApiErrorException as e:
            self._err_count += 1
            if self._err_count >= 2:
                self.state.is_online = False
            raise e

        self._err_count = 0
        self.state.is_online = True
        return rv

    def handle_command(self, command: str, resp, header):
        if header is not None and 'fwVer' in header:
            self.state.firmware_version = header['fwVer']
        if header is not None and 'hwVer' in header:
            self.state.hardware_version = header['hwVer']

        if 'data' not in resp:
            LOGGER.warning("No data provided for command: %s", command)
            return

        data = resp['data']
        LOGGER.debug("http: {} {}".format(command, data))

        if command in (
                'getBattery',
                'getChargeState',
                'getCleanCount',
                'getCleanInfo_V2',
                'getCleanPreference',
                'getError',
                'getSpeed',
                'getStats',
                'getTrueDetect',
                'getWaterInfo',
        ):
            # Handle overlapping commands via MQTT routines
            mq_command = 'on' + command.lstrip('get')
            self.handle_mqtt_message(mq_command, resp, log=False)

        elif command == 'getInfo':
            for key, value in data.items():
                self.handle_command(key, value, None)
        elif command == 'getTotalStats':
            self.state.total_stats = TotalStats(
                area=data['area'],
                time=data['time'],
                count=data['count'],
            )
        elif command == 'getLifeSpan':
            self.state.lifespan = [
                ComponentLifeSpan(component=x['type'], left=x['left'],
                                  total=x['total']) for x in data
            ]
        elif command == 'getCarpertPressure':
            self.state.auto_boost_suction_enabled = bool(data['enable'])
        elif command == 'getAutoEmpty':
            self.state.auto_empty_enabled = bool(data['enable'])
        else:
            LOGGER.warning(
                "Unhandled http command: {} {}".format(command, data))

    def _handle_state_change(self, state: VacuumState, attribute: str):
        for subscriber in self._subscribers:
            subscriber(state, attribute)

    def _start_polling(self):
        def poll_task():
            LOGGER.debug("Starting polling for %s", self._device.id)
            while self._should_poll:
                try:
                    self.force_refresh()
                except Exception as e:
                    LOGGER.exception("Error whilst polling, robot might be offline?")
                time.sleep(60 * 2)

            LOGGER.debug("Poll task for %s exiting", self._device.id)

        self._should_poll = True
        threading.Thread(target=poll_task, daemon=True).start()

    def _stop_polling(self):
        LOGGER.debug("Stopping polling for %s", self._device.id)
        self._should_poll = False

    def subscribe(self, handler):
        with self._lock:
            len_before = len(self._subscribers)
            self._subscribers.add(handler)
            if len_before == 0:
                # No subscribers! Subscribe and begin polling!
                self._subs_client.subscribe(
                    self._device, self.handle_mqtt_message)
                self._start_polling()

    def unsubscribe(self, handler):
        with self._lock:
            self._subscribers.remove(handler)
            if len(self._subscribers) == 0:
                # No subscribers left! Stop polling and unsubscribe
                self._stop_polling()
                self._subs_client.unsubscribe(
                    self._device, self.handle_mqtt_message)

    def set_true_detect(self, enabled: bool):
        self.exc_command('setTrueDetect', {
            'enable': int(enabled),
        })

    def set_clean_preference(self, enabled: bool):
        self.exc_command('setCleanPreference', {
            'enable': int(enabled),
        })

    def set_clean_count(self, count: bool):
        self.exc_command('setCleanCount', {
            'count': int(count),
        })

    def set_auto_empty(self, enabled: bool):
        self.exc_command('setAutoEmpty', {
            'enable': int(enabled),
        })
        # Optimistically update state, since no MQTT confirmation is received
        # for this setter.
        self.state.auto_empty_enabled = enabled

    def set_auto_boost_suction(self, enabled: bool):
        self.exc_command('setCarpertPressure', {
            'enable': int(enabled),
        })

    def set_water_level(self, level: VacuumState.WaterFlow):
        # TODO(NW): Colocate with deserialization and definition.
        water_level_map = {
            VacuumState.WaterFlow.LOW: 1,
            VacuumState.WaterFlow.MEDIUM: 2,
            VacuumState.WaterFlow.HIGH: 3,
            VacuumState.WaterFlow.ULTRA_HIGH: 4,
        }
        self.exc_command('setWaterInfo', {
            'amount': water_level_map[level],
        })

    def set_vacuum_speed(self, speed: VacuumState.Speed):
        # TODO(NW): Colocate with deserialization and definition.
        speed_map = {
            VacuumState.Speed.QUIET: 1000,
            VacuumState.Speed.STANDARD: 0,
            VacuumState.Speed.MAX: 1,
            VacuumState.Speed.MAX_PLUS: 2,
        }
        self.exc_command('setSpeed', {
            'speed': speed_map[speed],
        })

    def clean(self):
        self.exc_command('clean_V2', {
            "act": "start",
            "content": {
                "count": "",
                "donotClean": "",
                "type": "auto",
                "value": ""
            },
            "mode": "",
            "router": "plan"
        })

    def clean_areas(self, areas: List[int]):
        # TODO(NW): Use dynamic "count" for clean count.
        self.exc_command('clean_V2', {
            'act': 'start',
            'content': {
                'count': '',
                'donotClean': '',
                'type': 'spotArea',
                'value': ','.join(str(x) for x in areas),
            },
            'mode': '',
            'router': 'plan',
        })

    def clean_custom(self, custom_area: str):
        # TODO(NW): Use dynamic "count" for clean count.
        self.exc_command('clean_V2', {
            'act': 'start',
            'content': {
                'count': '',
                'donotClean': '',
                'type': 'customArea',
                'value': custom_area,
            },
            'mode': '',
            'router': 'plan',
        })

    def stop(self):
        # TODO(NW): See if the payload can be simplified (ala pause/resume)
        self.exc_command('clean_V2', {
            'act': 'stop',
            'content': {
                'count': '',
                'donotClean': '',
                'type': '',
                'value': ''
            },
            'mode': '',
            'router': 'plan'
        })

    def pause(self):
        self.exc_command('clean_V2', {'act': 'pause'})

    def resume(self):
        self.exc_command('clean_V2', {'act': 'resume'})

    def return_to_charge(self):
        self.exc_command('charge', {'act': 'go'})

    def relocate(self):
        self.exc_command('setRelocationState', {'mode': 'manu'})

    def play_sound(self, sound_id=30):
        self.exc_command('playSound', {"count": 1, "sid": sound_id})
