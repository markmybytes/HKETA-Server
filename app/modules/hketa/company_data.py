import asyncio
import csv
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Mapping

import aiohttp

try:
    from app.modules.hketa import api, enums, exceptions, models
except ImportError:
    import api
    import enums
    import exceptions
    import models


_TODAY = datetime.utcnow().isoformat(timespec="seconds")
"""Today's date (ISO-8601 datetime)"""


class CompanyData(ABC):
    """
        # Public Transport Company Data Retriver
        ~~~~~~~~~~~~~~~~~~~~~
        `CompanyData` is designed to retrive and store the data to local file system.

        It can work without storing the data to local.
        However, it will takes much longer time to retrive data and may fails due to API rate limit
    """

    class EnhancedJSONEncoder(json.JSONEncoder):
        """Encoder with dataclass support

        Reference: https://stackoverflow.com/a/51286749
        """

        def default(self, o):
            if is_dataclass(o):
                return asdict(o)
            return super().default(o)

    threshold: int
    """Threshold to determine an file is outdated (in day)"""

    is_store: bool
    """Indicator of storing routes data to local or not"""

    _root: os.PathLike
    """Root directory of the respective class (company/transportation)."""

    @property
    def routes_json(self) -> os.PathLike:
        """Path to \"routes\" data file name"""
        return os.path.join(self._root, "routes.json")

    @property
    def route_directory(self) -> os.PathLike:
        """Path to \"route\" data directory"""
        return os.path.join(self._root, "routes")

    @staticmethod
    def lang_key(locale: enums.Locale):
        match locale:
            case enums.Locale.TC:
                return "name_tc"
            case enums.Locale.EN:
                return "name_en"
            case _:
                raise KeyError(f"Undefined locale: {locale}.")

    def __init__(self, root: os.PathLike = None, store_local: bool = False, threshold: int = 30) -> None:
        if store_local and root is None:
            logging.error("No directory is provided for storing data files.")
            raise TypeError(
                "'store_local' is set to True but argument 'root' is missing")

        logging.debug(
            "Expiry threshold:\t%d\nStore to local:\t%s\nDirectory:\t%s",
            threshold, 'yes' if store_local else 'no', root)
        self.threshold = threshold
        self.is_store = store_local
        self._root = root

        if store_local and not os.path.exists(root):
            logging.info("'%s' does not exists, creating...", root)
            os.makedirs(os.path.join(root, "routes"))

    def is_outdated(self, fpath: str) -> bool:
        """Determine whether a data file is outdated.

        Args:
            fpath (str): File path

        Returns:
            bool: `true` if file not exists or outdated
        """
        print(fpath)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                lastupd = datetime.fromisoformat(json.load(f)['last_update'])
                return (datetime.utcnow() - lastupd).days > self.threshold
        else:
            return True

    @abstractmethod
    async def fetch_route(self, entry: models.RouteEntry) -> dict[str, Any]:
        """Retrive stop list and data of the `route`

        Returns:
            >>> example
            {
                'last_update': "ISO-8601 datetime",
                'data': {
                    '<stop code>': {
                        'seq': int,
                        'name_tc': str,
                        'name_en': str
                    }
                }
            }

        """

    @abstractmethod
    async def fetch_routes(self) -> dict[str, Any]:
        """Retrive all route list and data operating by the operator

        Returns:
            >>> example
            {
                'last_update': "ISO-8601 datetime",
                'data': {
                    '<stop code>': {
                        'seq': int,
                        'name_tc': str,
                        'name_en': str
                    }
                }
            }
        """

    def route(self, entry: models.RouteEntry) -> dict[str, Any]:
        """Retrive stop list and data of the `route`. Create/update the data file if necessary

        Args:
            entry (route_entry.RouteEntry): Target route
        """
        fpath = os.path.join(self.route_directory, self.route_fname(entry))

        if not self.is_store:
            logging.info(
                "Retiving %s route data (no store is set)", entry.name)
            return asyncio.run(self.fetch_route(entry))

        if self.is_outdated(fpath):
            logging.info(
                "%s local route data is outdated, refetching...", entry.name)

            data = asyncio.run(self.fetch_route(entry))
            self._write_route(entry, data)
            return data
        else:
            with open(fpath, "r", encoding="utf-8") as f:
                logging.debug("loading %s route data from %s",
                              entry.name, fpath)
                return json.load(f,)

    def routes(self) -> dict[str, Any]:
        """Retrive all route list and data operating by the operator. Create/update when necessary
        """
        if not self.is_store:
            logging.info(
                "retiving %s routes data (no store is set)", type(self).__name__)
            return asyncio.run(self.fetch_routes())
        elif self.is_outdated(self.routes_json):
            logging.info(
                "%s local routes data is outdated or not exists, updating...", type(self).__name__)

            data = asyncio.run(self.fetch_routes())
            self._write_routes(data)
            return data
        else:
            with open(self.routes_json, "r", encoding="utf-8") as f:
                logging.debug("reading %s route data from %s",
                              type(self).__name__, self.routes_json)
                return json.load(f)

    def route_fname(self, entry: models.RouteEntry) -> str:
        """Get file name of target `entry` stop data

        Args:
            entry (route_entry.RouteEntry): Target route

        Returns:
            str: Name of the route data file 
                (e.g. "1A-outbound-1.json", "TML-outbound.json")
        """
        return f"{entry.name}-{entry.direction.value}-{entry.service_type}.json"

    def _write_route(self, entry: models.RouteEntry, data: Mapping) -> None:
        """Write `data` (route data) to persistent storage

        Args:
            entry (route_entry.RouteEntry): The route which the `data` belongs to
            data (Mapping): Stop list and data of the `route`
        """
        fpath = os.path.join(self.route_directory, self.route_fname(entry))

        with open(fpath, "w", encoding="utf-8") as f:
            logging.info("writing %s route data to %s",
                         type(self).__name__, fpath)
            json.dump(data, f, indent=4, cls=self.EnhancedJSONEncoder)

    def _write_routes(self, data: Mapping) -> None:
        """Write `data` (routes data) to persistent storage

        Args:
            data (Mapping): Routes list and data of the operator
        """
        with open(self.routes_json, "w", encoding="utf-8") as f:
            logging.info("writing %s routes data to %s",
                         type(self).__name__, self.routes_json)
            json.dump(data, f, indent=4, cls=self.EnhancedJSONEncoder)


class KMBData(CompanyData):

    _direction = {
        'O': enums.Direction.OUTBOUND.value,
        'I': enums.Direction.INBOUND.value,
        enums.Direction.OUTBOUND.value: "I",
        enums.Direction.INBOUND.value: "O"
    }
    """direction text translation to `hketa.enums.Direction`"""

    def __init__(self, root: os.PathLike = None, store_local: bool = False, threshold: int = 30) -> None:
        super().__init__(os.path.join(root, "kmb"), store_local, threshold)

    async def fetch_routes(self) -> dict:
        async def fetch_route_details(session: aiohttp.ClientSession,
                                      stop: dict) -> dict:
            direction = self._direction[stop['bound']]
            stop_list = (await api.kmb_route_stop_list(
                stop['route'], direction, stop['service_type'], session))['data']
            return {
                'route': stop['route'],
                'direction': direction,
                'terminals': models.Terminal.Detail(
                    stop['service_type'],
                    models.Terminal.Stop(
                        stop_list[0]['stop'],
                        {
                            enums.Locale.EN: stop['orig_en'],
                            enums.Locale.TC: stop['orig_tc'],
                        }
                    ),
                    models.Terminal.Stop(
                        stop_list[-1]['stop'],
                        {
                            enums.Locale.EN: stop['dest_en'],
                            enums.Locale.TC: stop['dest_tc'],
                        }
                    ),
                )
            }
        # generate output
        output = {'last_update': _TODAY, 'data': {}}

        async with aiohttp.ClientSession() as session:
            tasks = [fetch_route_details(session, stop)
                     for stop in (await api.kmb_route_list(session))['data']]

            for entry in await asyncio.gather(*tasks):
                # route name
                output['data'].setdefault(entry['route'], {})
                # direction
                output['data'][entry['route']].setdefault(
                    entry['direction'], [])

                # service type
                output['data'][entry['route']][entry['direction']].append(
                    entry['terminals']
                )
        return output

    async def fetch_route(self, entry: models.RouteEntry) -> dict:
        async def fetch_stop_details(session: aiohttp.ClientSession, stop: dict):
            """Get stop detail by stop ID
            """
            dets = (await api.kmb_stop_details(stop['stop'], session))['data']
            return models.Stop(
                stop['stop'],
                int(stop['seq']),
                {
                    enums.Locale.TC: dets.get('name_tc'),
                    enums.Locale.EN: dets.get('name_en'),
                }
            )

        async with aiohttp.ClientSession() as session:
            stop_list = await api.kmb_route_stop_list(
                entry.name,
                entry.direction.value,
                entry.service_type, session)

            data = {
                'last_update': _TODAY,
                'data': await asyncio.gather(
                    *[fetch_stop_details(session, stop) for stop in stop_list['data']])
            }

            if len(data['data']) == 0:
                raise exceptions.RouteNotExist()
            return data

    def route_fname(self, entry: models.RouteEntry):
        return f"{entry.name}-{entry.direction.value}-{entry.service_type}.json"


class MTRLrtData(CompanyData):

    _direction = {
        enums.Direction.OUTBOUND.value: "1",
        enums.Direction.INBOUND.value: "2",
        '1': enums.Direction.OUTBOUND.value,
        '2': enums.Direction.INBOUND.value
    }
    """Direction text translation"""

    def __init__(self, root: os.PathLike = None, store_local: bool = False, threshold: int = 30) -> None:
        super().__init__(os.path.join(root, "mtr_lrt"), store_local, threshold)

    async def fetch_routes(self) -> dict:
        output = {'last_update': _TODAY, 'data': {}}

        apidata = csv.reader(await api.mtr_lrt_route_stop_list())
        next(apidata)  # ignore the header line

        for row in apidata:
            # [0]route, [1]direction , [2]stopCode, [3]stopID, [4]stopTCName, [5]stopENName, [6]seq
            output['data'].setdefault(row[0], {
                enums.Direction.INBOUND: [],
                enums.Direction.OUTBOUND: [],
            })

            direction = self._direction[row[1]]

            if (row[6] == "1.00"):
                # original
                output['data'][row[0]][direction].append(
                    {'service_type': None})
                output['data'][row[0]][direction][0]['orig'] = models.Terminal.Stop(
                    row[3],
                    {
                        enums.Locale.EN: row[5],
                        enums.Locale.TC: row[4]
                    }
                )
            else:
                # destination
                output['data'][row[0]][direction][0]['dest'] = models.Terminal.Stop(
                    row[3],
                    {
                        enums.Locale.EN: row[5],
                        enums.Locale.TC: row[4]
                    }
                )

        return output

    async def fetch_route(self, entry: models.RouteEntry) -> dict:
        apidata = csv.reader(await api.mtr_lrt_route_stop_list())
        stops = [stop for stop in apidata
                 if stop[0] == str(entry.name)
                 and self._direction[stop[1]] == entry.direction]

        if len(stops) == 0:
            raise exceptions.RouteNotExist()
        return {
            'last_update': _TODAY,
            'data': [
                models.Stop(
                    row[3],
                    int(row[6].strip('.00')),
                    {enums.Locale.TC: row[4], enums.Locale.EN: row[5]}
                ) for row in stops
            ]
        }


class MTRTrainData(CompanyData):

    _direction = {
        'DT': enums.Direction.DOWNLINK.value,
        'UT': enums.Direction.UPLINK.value,
        enums.Direction.DOWNLINK.value: "DT",
        enums.Direction.UPLINK.value: "UT",
    }
    """Direction text translation"""

    def __init__(self, root: os.PathLike = None, store_local: bool = False, threshold: int = 30) -> None:
        super().__init__(os.path.join(root, "mtr_train"), store_local, threshold)

    async def fetch_routes(self) -> dict:
        output = {'last_update': _TODAY, 'data': {}}

        apidata = csv.reader(await api.mtr_train_route_stop_list())
        next(apidata)  # ignore header line

        for row in apidata:
            # [0]Line Code [1]Direction [2]Station Code [3]Station ID [4]Chinese Name [5]English Name [6]Sequence
            if not any(row):  # skip empty row
                continue

            direction, _, type_ = row[1].partition("-")
            if type_:  # route with multiple origin/destination
                direction, type_ = type_, direction  # e.g. LMC-DT
                # make a "new line" for these type of route
                row[0] += f"-{type_}"
            direction = self._direction[direction]

            output['data'].setdefault(row[0], {})
            output['data'][row[0]].setdefault(direction, [])

            if (row[6] == "1.00"):
                # origin
                output['data'][row[0]][direction].append(
                    {'service_type': None})
                output['data'][row[0]][direction][0]['orig'] = models.Terminal.Stop(
                    row[2],
                    {
                        enums.Locale.EN: row[5],
                        enums.Locale.TC: row[4]
                    }
                )
            else:
                # destination
                output['data'][row[0]][direction][0]['dest'] = models.Terminal.Stop(
                    row[2],
                    {
                        enums.Locale.EN: row[5],
                        enums.Locale.TC: row[4]
                    }
                )

        return output

    async def fetch_route(self, entry: models.RouteEntry) -> dict:
        apidata = csv.reader(await api.mtr_train_route_stop_list())

        if "-" in entry.name:
            # route with multiple origin/destination
            rtname, type_ = entry.name.split("-")
            stops = [stop for stop in apidata
                     if stop[0] == rtname
                     and type_ in stop[1]]
        else:
            stops = [stop for stop in apidata
                     if stop[0] == str(entry.name)
                     and self._direction[stop[1]] == entry.direction]

        if len(stops) == 0:
            raise exceptions.RouteNotExist()
        return {
            'last_update': _TODAY,
            'data': [
                models.Stop(
                    stop[2],
                    int(float(stop[-1])),
                    {enums.Locale.TC: stop[4], enums.Locale.EN: stop[5]}
                ) for stop in stops
            ]
        }


class MTRBusData(CompanyData):

    _direction = {
        'O': enums.Direction.OUTBOUND.value,
        'I': enums.Direction.INBOUND.value,
        enums.Direction.OUTBOUND.value: "I",
        enums.Direction.INBOUND.value: "O"
    }
    """direction text translation to `hketa.enums`"""

    def __init__(self, root: os.PathLike = None, store_local: bool = False, threshold: int = 30) -> None:
        super().__init__(os.path.join(root, "mtr_bus"), store_local, threshold)

    async def fetch_routes(self) -> dict:
        output = {'last_update': _TODAY, 'data': {}}

        apidata = csv.reader(await api.mtr_bus_stop_list())
        next(apidata)  # ignore header line

        for stop in apidata:
            # [0]route, [1]direction, [2]seq, [3]stopID, [4]stopLAT, [5]stopLONG, [6]stopTCName, [7]stopENName
            direction = self._direction[stop[1]]

            output['data'].setdefault(stop[0], {})
            output['data'][stop[0]].setdefault(direction, [])

            if stop[2] == "1.00":
                # orignal
                output['data'][stop[0]][direction].append(
                    {'service_type': None})
                output['data'][stop[0]][direction][0]['orig'] = models.Terminal.Stop(
                    stop[3],
                    {
                        enums.Locale.EN: stop[7],
                        enums.Locale.TC: stop[6]
                    }
                )
            else:
                # destination
                output['data'][stop[0]][direction][0]['dest'] = models.Terminal.Stop(
                    stop[3],
                    {
                        enums.Locale.EN: stop[7],
                        enums.Locale.TC: stop[6]
                    }
                )

        return output

    async def fetch_route(self, entry: models.RouteEntry) -> dict:
        async with aiohttp.ClientSession() as session:
            apidata = csv.reader(await api.mtr_bus_stop_list(session))

        stops = [stop for stop in apidata
                 if stop[0] == str(entry.name) and self._direction[stop[1]] == entry.direction]

        if len(stops) == 0:
            raise exceptions.RouteNotExist()
        return {
            'last_update': _TODAY,
            'data': [
                models.Stop(
                    stop[3],
                    int(float(stop[2])),
                    {enums.Locale.TC: stop[6], enums.Locale.EN: stop[7]}
                ) for stop in stops
            ]
        }


class CityBusData(CompanyData):

    def __init__(self, root: os.PathLike = None, store_local: bool = False, threshold: int = 30) -> None:
        super().__init__(os.path.join(root, "ctb"), store_local, threshold)

    async def fetch_routes(self) -> dict:
        async def fetch_route_details(session: aiohttp.ClientSession,
                                      entry: dict) -> dict:
            # async helper, get stop details + stop ID
            directions = {
                'inbound': (await api.bravobus_route_stop_list(
                    "ctb", entry['route'], "inbound", session))['data'],
                'outbound': (await api.bravobus_route_stop_list(
                    "ctb", entry['route'], "outbound", session))['data']
            }

            routes = {entry['route']: {}}
            for direction, stop_list in directions.items():
                if len(stop_list) == 0:
                    continue

                ends = await asyncio.gather(*[
                    api.bravobus_stop_details(stop_list[0]['stop']),
                    api.bravobus_stop_details(stop_list[-1]['stop'])
                ])

                routes[entry['route']].setdefault(direction, [
                    models.Terminal.Detail(
                        None,
                        models.Terminal.Stop(
                            stop_list[0]["stop"],
                            {
                                enums.Locale.EN: ends[0]['data'].get('name_en', "N/A"),
                                enums.Locale.TC:  ends[0]['data'].get('name_tc', "未有資料"),
                            }
                        ),
                        models.Terminal.Stop(
                            stop_list[0]["stop"],
                            {
                                enums.Locale.EN: ends[-1]['data'].get('name_en', "N/A"),
                                enums.Locale.TC:  ends[-1]['data'].get('name_tc', "未有資料"),
                            }
                        ),

                    )
                ])

            return routes

        async with aiohttp.ClientSession() as session:
            tasks = [fetch_route_details(session, stop) for stop in
                     (await api.bravobus_route_list("ctb", session))['data']]

            # keys()[0] = route name

            return {
                'last_update': _TODAY,
                'data': {list(entry.keys())[0]: entry[list(entry.keys())[0]]
                         for entry in await asyncio.gather(*tasks)}}

    async def fetch_route(self, entry: models.RouteEntry) -> dict:
        async def fetch_stop_details(session: aiohttp.ClientSession, stop: dict):
            # async helper, get stop details
            dets = (await api.bravobus_stop_details(stop['stop'], session))['data']
            return models.Stop(
                stop['stop'],
                int(stop['seq']),
                {
                    enums.Locale.TC: dets.get('name_tc', "未有資料"),
                    enums.Locale.EN: dets.get('name_en', "N/A")
                }
            )

        async with aiohttp.ClientSession() as session:
            stop_list = await api.bravobus_route_stop_list(
                "ctb",
                entry.name,
                entry.direction,
                session)

            data = {
                'last_update': _TODAY,
                'data': await asyncio.gather(
                    *[fetch_stop_details(session, stop) for stop in stop_list['data']])
            }

            if len(data['data']) == 0:
                raise exceptions.RouteNotExist()
            return data


if __name__ == "__main__":
    entry_ = models.RouteEntry(
        enums.Company.KMB, "265M", enums.Direction.OUTBOUND, "1", "223DAE7E925E3BB9", enums.Locale.TC)
    route = MTRBusData("caches\\transport_data", True)
    route.routes()
