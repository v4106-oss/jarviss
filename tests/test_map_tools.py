import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from app.core.agent import ActionExecutor, PlannedAction
from app.core.ai import AI, TOOL_DECLARATIONS
from app.core.map_tools import MapError, MapTools
from app.core.poi_store import (
    PoiStore,
    category_label,
    fold_tr,
    infer_name_category,
    ui_kind,
)
from app.core.safety import (
    ActionForbidden,
    AuditLog,
    KillSwitch,
    PathPolicy,
    PermissionGate,
    Risk,
    SafetyServices,
    UndoStore,
    classify_action,
)
from app.core.settings import Settings


class FakeResponse:
    def __init__(self, payload=None, text: str = ""):
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("json yok")
        return self._payload


class FakeSession:
    def __init__(self, router):
        self.router = router
        self.headers = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.router(method, url, kwargs)
        if isinstance(result, Exception):
            raise result
        return result


class StubSettings:
    def __init__(self, home=None):
        self._home = home
        self.saved = None

    @property
    def home_is_set(self):
        return self._home is not None

    @property
    def home_location(self):
        return dict(self._home) if self._home else {
            "name": "Derince", "region": "Kocaeli", "lat": 40.757, "lon": 29.815,
        }

    def set_home(self, lat, lon, name="Evim", region="Kocaeli"):
        self._home = {"name": name, "region": region, "lat": float(lat), "lon": float(lon)}
        self.saved = dict(self._home)
        return dict(self._home)


def tools_with(router, settings=None, listener=None):
    session = FakeSession(router)
    return MapTools(
        settings=settings or StubSettings(),
        listener=listener,
        session=session,
        sleep=lambda _: None,
    ), session


class SettingsHomeTests(unittest.TestCase):
    def test_home_defaults_and_persists_to_local_appdata(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"LOCALAPPDATA": directory}
        ):
            settings = Settings()
            self.assertFalse(settings.home_is_set)
            self.assertEqual(settings.home_location["name"], "Derince")

            saved = settings.set_home(40.76012, 29.92841, "İzmit Ev", "Kocaeli")
            self.assertTrue(settings.home_is_set)
            self.assertEqual(saved["name"], "İzmit Ev")

            reloaded = Settings()
            self.assertTrue(reloaded.home_is_set)
            self.assertAlmostEqual(reloaded.home_location["lat"], 40.76012)
            self.assertAlmostEqual(reloaded.home_location["lon"], 29.92841)
            self.assertEqual(reloaded.home_location["name"], "İzmit Ev")

    def test_agent_limits_default_to_unlimited_and_persist(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"LOCALAPPDATA": directory}
        ):
            settings = Settings()
            self.assertEqual(settings.agent_step_limit, 0)
            self.assertEqual(settings.agent_seconds_limit, 0)
            self.assertEqual(settings.agent_loop_limit, 0)

            settings.set_agent_limits(step=30, seconds=240, loop=5)
            reloaded = Settings()
            self.assertEqual(reloaded.agent_step_limit, 30)
            self.assertEqual(reloaded.agent_seconds_limit, 240)
            self.assertEqual(reloaded.agent_loop_limit, 5)

    def test_agent_limits_are_clamped_to_zero(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"LOCALAPPDATA": directory}
        ):
            settings = Settings()
            settings.set_agent_limits(step=-5, seconds=-1, loop=-9)
            self.assertEqual(settings.agent_step_limit, 0)
            self.assertEqual(settings.agent_seconds_limit, 0)
            self.assertEqual(settings.agent_loop_limit, 0)

    def test_invalid_home_coordinates_are_refused(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"LOCALAPPDATA": directory}
        ):
            settings = Settings()
            with self.assertRaises(ValueError):
                settings.set_home(140.0, 29.0)
            self.assertFalse(settings.home_is_set)


class GeocodeTests(unittest.TestCase):
    def test_geocode_emits_focus_payload_and_formats_results(self):
        payloads = []

        def router(method, url, kwargs):
            self.assertIn("nominatim", url)
            self.assertEqual(kwargs["params"]["accept-language"], "tr")
            return FakeResponse([{
                "lat": "40.7591", "lon": "29.9187", "type": "suburb",
                "display_name": "İzmit, Kocaeli, Türkiye",
                "address": {"state": "Kocaeli", "town": "İzmit", "suburb": "Kemalpaşa"},
            }])

        tools, session = tools_with(router, listener=payloads.append)
        result = tools.run({"action": "geocode", "query": "İzmit"})

        self.assertIn("📍", result)
        self.assertIn("İzmit", result)
        self.assertEqual(payloads[-1]["kind"], "focus")
        self.assertAlmostEqual(payloads[-1]["center"]["lat"], 40.7591)
        self.assertIn("JarvisDesktop", session.calls[0][2]["headers"]["User-Agent"])

    def test_empty_geocode_result_returns_turkish_error(self):
        tools, _ = tools_with(lambda method, url, kwargs: FakeResponse([]))
        result = tools.run({"action": "geocode", "query": "olmayan-yer"})
        self.assertIn("konum bulunamadı", result)
        with self.assertRaises(MapError):
            tools.geocode_all("olmayan-yer")


class RouteTests(unittest.TestCase):
    def test_walking_route_uses_osrm_foot_and_emits_route(self):
        payloads = []

        def router(method, url, kwargs):
            self.assertIn("routed-foot", url)
            self.assertEqual(kwargs["params"]["geometries"], "geojson")
            return FakeResponse({
                "code": "Ok",
                "routes": [{
                    "distance": 3240.0,
                    "duration": 2530.0,
                    "geometry": {"coordinates": [[29.815, 40.757], [29.86, 40.77], [29.9187, 40.7591]]},
                    "legs": [{"steps": [
                        {"distance": 320, "name": "Bağdat Caddesi", "maneuver": {"type": "depart"}},
                        {"distance": 2920, "name": "", "maneuver": {"type": "turn", "modifier": "left"}},
                        {"distance": 0, "name": "", "maneuver": {"type": "arrive"}},
                    ]}],
                }],
            })

        tools, session = tools_with(router, listener=payloads.append)
        result = tools.run({
            "action": "route",
            "destination": {"name": "İzmit", "lat": 40.7591, "lon": 29.9187},
            "mode": "yürüyerek",
        })

        self.assertIn("Yaya rotası", result)
        self.assertIn("3.2 km", result)
        self.assertIn("42 dk", result)
        self.assertEqual(payloads[-1]["kind"], "route")
        self.assertEqual(len(payloads[-1]["points"]), 3)
        self.assertEqual(payloads[-1]["mode"], "walking")
        self.assertIn("Bağdat Caddesi", result)
        self.assertTrue(session.calls[0][1].startswith("https://routing.openstreetmap.de/routed-foot/"))

    def test_route_mode_aliases(self):
        self.assertEqual(MapTools.normalize_mode("Bisiklet"), "cycling")
        self.assertEqual(MapTools.normalize_mode("araba"), "driving")
        self.assertEqual(MapTools.normalize_mode("bilinmeyen"), "driving")


class NearbyTests(unittest.TestCase):
    def test_category_search_uses_overpass_and_sorts_by_distance(self):
        payloads = []

        def router(method, url, kwargs):
            self.assertIn("overpass", url)
            self.assertIn('amenity"="pharmacy', kwargs["data"]["data"])
            return FakeResponse({"elements": [
                {"type": "node", "id": 2, "lat": 40.7600, "lon": 29.8160, "tags": {
                    "name": "Uzak Eczane", "amenity": "pharmacy",
                }},
                {"type": "node", "id": 1, "lat": 40.7572, "lon": 29.8152, "tags": {
                    "name": "Yakın Eczane", "amenity": "pharmacy",
                    "addr:street": "Cumhuriyet Caddesi", "opening_hours": "09:00-19:00",
                    "phone": "+90 262 000 00 00",
                }},
            ]})

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.run({"action": "nearby", "query": "eczane", "radius": 1200})

        self.assertIn("Yakın Eczane", result)
        self.assertLess(result.index("Yakın Eczane"), result.index("Uzak Eczane"))
        self.assertIn("uydurulmaz", result)
        self.assertEqual(payloads[-1]["kind"], "places")
        self.assertEqual(payloads[-1]["items"][0]["name"], "Yakın Eczane")
        self.assertIn("google.com/maps", payloads[-1]["items"][0]["maps_url"])
        self.assertEqual(payloads[-1]["items"][0]["category"], "Eczane")
        self.assertEqual(payloads[-1]["items"][0]["phone"], "+90 262 000 00 00")
        self.assertEqual(payloads[-1]["items"][0]["open"], "09:00-19:00")

    def test_mall_category_query_returns_avm_items(self):
        payloads = []
        queries = []

        def router(method, url, kwargs):
            queries.append(kwargs["data"]["data"])
            return FakeResponse({"elements": [
                {"type": "node", "id": 9, "lat": 40.7603, "lon": 29.8204, "tags": {
                    "name": "Test AVM", "shop": "mall",
                    "opening_hours": "Mo-Su 10:00-22:00",
                }},
            ]})

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.nearby(
            "avm", center={"lat": 40.76, "lon": 29.82},
            radius=4000, limit=10, retries=0,
        )

        self.assertEqual(result["items"][0]["name"], "Test AVM")
        self.assertIn('shop"="mall', queries[-1])
        self.assertEqual(payloads[-1]["items"][0]["category"], "AVM")
        self.assertEqual(payloads[-1]["items"][0]["open"], "Mo-Su 10:00-22:00")

    def test_set_home_persists_and_emits_home(self):
        payloads = []
        settings = StubSettings()
        tools, _ = tools_with(lambda method, url, kwargs: FakeResponse({}), settings, payloads.append)

        result = tools.run({
            "action": "set_home", "lat": 40.765, "lon": 29.91, "name": "Yeni Ev",
        })

        self.assertIn("Ev konumu kaydedildi", result)
        self.assertEqual(settings.saved["name"], "Yeni Ev")
        self.assertAlmostEqual(settings.saved["lat"], 40.765)
        self.assertEqual(payloads[-1]["kind"], "home")


class RetryResponse:
    def __init__(self, status, retry_after=None, payload=None):
        self.status_code = status
        self.headers = {"Retry-After": retry_after} if retry_after else {}
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("json yok")
        return self._payload


class RequestResilienceTests(unittest.TestCase):
    def test_request_retries_after_429_and_recovers(self):
        calls = []
        sleeps = []

        def router(method, url, kwargs):
            calls.append(url)
            if len(calls) < 3:
                return RetryResponse(429, retry_after="1")
            return FakeResponse({"elements": []})

        tools = MapTools(session=FakeSession(router), sleep=sleeps.append)
        response = tools._request("GET", "https://overpass-api.de/api/interpreter", data={"data": "x"})

        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertAlmostEqual(sleeps[0], 1.0)
        self.assertEqual(response.json(), {"elements": []})

    def test_request_reports_temporary_overload_after_retries(self):
        tools = MapTools(
            session=FakeSession(lambda method, url, kwargs: RetryResponse(429)),
            sleep=lambda _: None,
        )
        with self.assertRaises(MapError) as context:
            tools._request("GET", "https://overpass-api.de/api/interpreter")
        self.assertIn("geçici olarak yanıt veremedi", str(context.exception))

    def test_request_retries_timeouts_then_reports_turkish_error(self):
        sleeps = []

        def router(method, url, kwargs):
            raise requests.Timeout("gecikti")

        tools = MapTools(session=FakeSession(router), sleep=sleeps.append)
        with self.assertRaises(MapError) as context:
            tools._request("GET", "https://overpass-api.de/api/interpreter")
        self.assertIn("zaman aşımı", str(context.exception))
        self.assertEqual(len(sleeps), 2)


class ClickNearbyTests(unittest.TestCase):
    @staticmethod
    def osm_xml(*nodes):
        body = "".join(
            f'<node id="{index}" lat="{lat}" lon="{lon}">'
            + "".join(f'<tag k="{key}" v="{value}"/>' for key, value in tags.items())
            + "</node>"
            for index, (lat, lon, tags) in enumerate(nodes, 1)
        )
        return f'<osm version="0.6">{body}</osm>'

    def test_click_nearby_uses_fast_osm_api_and_caches(self):
        payloads = []
        calls = []

        def router(method, url, kwargs):
            calls.append(url)
            self.assertIn("api.openstreetmap.org", url)
            return FakeResponse(text=self.osm_xml(
                (40.7572, 29.8152, {
                    "name": "Yakın Park", "leisure": "park",
                    "wikimedia_commons": "File:Cinarli_Park.jpg",
                }),
                (40.7580, 29.8160, {
                    "name": "Köşe Market", "shop": "convenience", "price": "₺",
                }),
            ))

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.click_nearby(40.757, 29.815, 15)
        tools.click_nearby(40.757, 29.815, 15)

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["center"]["name"], "Yakın Park")
        items = result["items"]
        self.assertEqual([item["name"] for item in items], ["Yakın Park", "Köşe Market"])
        self.assertIn("commons.wikimedia.org", items[0]["photo"])
        self.assertEqual(items[1]["price"], "₺")
        self.assertEqual(payloads[-1]["kind"], "places")

    def test_click_nearby_scales_radius_with_zoom_and_rejects_bad_coords(self):
        queries = []

        def router(method, url, kwargs):
            if "api.openstreetmap" in url:
                raise requests.ConnectionError("osm api kapalı")
            queries.append(kwargs["data"]["data"])
            return FakeResponse({"elements": []})

        tools, _ = tools_with(router)
        tools.click_nearby(40.757, 29.815, 18)
        tools.click_nearby(40.757, 29.815, 12)
        self.assertIn("around:60,", queries[0])
        self.assertIn("around:1200,", queries[1])
        with self.assertRaises(MapError):
            tools.click_nearby(95.0, 29.815)

    def test_click_nearby_includes_military_and_government_keys(self):
        queries = []

        def router(method, url, kwargs):
            if "api.openstreetmap" in url:
                raise requests.ConnectionError("osm api kapalı")
            queries.append(kwargs["data"]["data"])
            return FakeResponse({"elements": [
                {"type": "way", "id": 9, "lat": 40.7571, "lon": 29.8151, "tags": {
                    "name": "Jandarma Komutanlığı", "landuse": "military",
                }},
            ]})

        tools, _ = tools_with(router)
        result = tools.click_nearby(40.757, 29.815, 15)

        query = queries[0]
        for key in ("military", "landuse", "government", "building"):
            self.assertIn(f'["{key}"]', query)
        self.assertEqual(result["items"][0]["name"], "Jandarma Komutanlığı")
        self.assertEqual(result["items"][0]["category"], "Askeri tesis")

    def test_click_nearby_falls_back_to_overpass_when_osm_api_fails(self):
        queries = []

        def router(method, url, kwargs):
            if "api.openstreetmap" in url:
                raise requests.ConnectionError("osm api kapalı")
            queries.append(kwargs["data"]["data"])
            return FakeResponse({"elements": [
                {"type": "node", "id": 3, "lat": 40.7572, "lon": 29.8152,
                 "tags": {"name": "Eczane", "amenity": "pharmacy"}},
            ]})

        tools, _ = tools_with(router)
        result = tools.click_nearby(40.757, 29.815, 16)

        self.assertEqual(len(queries), 1)
        self.assertEqual(result["items"][0]["name"], "Eczane")


class PhotoPriceTests(unittest.TestCase):
    def test_mall_stores_carry_price_and_photo(self):
        elements = [{
            "type": "node", "id": 5, "lat": 40.76, "lon": 29.81,
            "tags": {
                "name": "Alaçatı Muhallebicisi", "shop": "deli",
                "price": "₺₺",
                "wikimedia_commons": "File:Alacati_Muhallebi.jpg",
            },
        }]
        stores = MapTools._parse_mall_stores(elements, "Arasta Park AVM")
        self.assertEqual(stores[0]["price"], "₺₺")
        self.assertIn("Special:FilePath/Alacati_Muhallebi.jpg", stores[0]["photo"])

    def test_photo_url_accepts_only_wikimedia_sources(self):
        url = MapTools._photo_url({"image": "File:Deneme Foto.jpg"})
        self.assertIn("commons.wikimedia.org/wiki/Special:FilePath/Deneme_Foto.jpg", url)
        self.assertEqual(
            MapTools._photo_url({"image": "https://upload.wikimedia.org/x.jpg"}),
            "https://upload.wikimedia.org/x.jpg",
        )
        self.assertEqual(MapTools._photo_url({"image": "https://izleme.site/x.jpg"}), "")
        self.assertEqual(MapTools._photo_url({}), "")

    def test_mall_own_tags_extracts_photo(self):
        elements = [{
            "type": "way", "id": "123456",
            "tags": {"name": "Arasta Park AVM", "shop": "mall", "image": "File:Arasta.jpg"},
        }]
        mall = {"osm_type": "way", "osm_id": "123456", "name": "Arasta Park AVM"}
        tags = MapTools._mall_own_tags(elements, mall)
        self.assertIn("commons.wikimedia.org", MapTools._photo_url(tags))

        self.assertEqual(MapTools._mall_own_tags(elements, {
            "osm_type": "relation", "osm_id": "999", "name": "Arasta Park AVM",
        }), elements[0]["tags"])


class KocaeliParsingTests(unittest.TestCase):
    def test_nearest_stops_and_security_header(self):
        def router(method, url, kwargs):
            self.assertIn("yakin_duraklar.php", url)
            self.assertEqual(kwargs["data"]["func"], "ns")
            self.assertIn("X-Security-Token", kwargs["headers"])
            return FakeResponse(text=json.dumps({
                "id": ["101", "102"], "name": ["Derince Merkez", "Derince İskele"],
                "lat": ["40.7571", "40.7602"], "lon": ["29.8153", "29.8188"],
                "dist": ["0.12", "0.45"],
            }))

        tools, _ = tools_with(router)
        stops = tools._nearest_stops(40.757, 29.815)
        self.assertEqual([stop["stop_id"] for stop in stops], ["101", "102"])
        self.assertEqual(stops[0]["distance_m"], 120)
        self.assertTrue(stops[0]["name"].startswith("Derince"))

    def test_bus_stops_emits_map_markers(self):
        payloads = []

        def router(method, url, kwargs):
            return FakeResponse(text=json.dumps({
                "id": ["101"], "name": ["Derince Merkez"],
                "lat": ["40.7571"], "lon": ["29.8153"], "dist": ["0.12"],
            }))

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.run({"action": "bus_stops", "radius": 1500})
        self.assertIn("101 · Derince Merkez", result)
        self.assertEqual(payloads[-1]["kind"], "places")
        self.assertEqual(payloads[-1]["items"][0]["kind"], "stop")
        self.assertEqual(payloads[-1]["items"][0]["category"], "Durak")
        self.assertEqual(payloads[-1]["items"][0]["stop_id"], "101")

    def test_stop_details_emits_lines_and_live_info(self):
        payloads = []
        stop_json = json.dumps({
            "busStopid": "101", "busStopName": "Derince Merkez",
            "lat": 40.7571, "lng": 29.8153,
        })
        passing = (
            '<a href="#" onclick="openRouteLinkMain(\'10\')">10 · Derince - İzmit</a>'
        )
        live = (
            '<input type="hidden" value="40.7601+29.8172+<center>10<br/>34 ABC 123</center>'
            '+x+10+3+5"/>'
        )

        def router(method, url, kwargs):
            if "searchSmartStopJSON" in url:
                return FakeResponse(text=stop_json)
            if "searchSmartStop" in url:
                return FakeResponse(text=passing)
            if "searchBusesontheStop" in url:
                return FakeResponse(text=live)
            raise AssertionError(f"beklenmeyen istek: {url}")

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.run({"action": "bus_stop", "stop_id": "101"})

        self.assertIn("Derince Merkez", result)
        item = payloads[-1]["items"][0]
        self.assertEqual(payloads[-1]["kind"], "places")
        self.assertEqual(item["category"], "Durak")
        self.assertEqual(item["stop_id"], "101")
        self.assertEqual(item["lines"], ["10"])
        self.assertEqual(item["live"], [{"code": "10", "eta_min": 5}])

    def test_pass_lines_live_buses_and_route_stops_parsers(self):
        passes = (
            '<a href="#" onclick="openRouteLinkMain(\'10\')">10 · Derince - İzmit</a>'
            '<a href="#" onclick="openRouteLinkMain(\'10\')">tekrar</a>'
            '<a href="#" onclick="openRouteLinkMain(\'200\')">200 · Otogar</a>'
        )
        lines = MapTools.parse_pass_lines(passes)
        self.assertEqual([line["code"] for line in lines], ["10", "200"])

        live = (
            '<input type="hidden" value="40.7601+29.8172+<center>10<br/>34 ABC 123</center>'
            '+x+10+3+5"/>'
        )
        buses = MapTools.parse_live_buses(live)
        self.assertEqual(buses[0]["route_code"], "10")
        self.assertEqual(buses[0]["vehicle"], "34 ABC 123")
        self.assertEqual(buses[0]["stops_away"], 3)
        self.assertEqual(buses[0]["eta_min"], 5)

        stops = MapTools.parse_route_stops(
            '<input value="101+Derince Merkez+40.7571+29.8153" />'
            '<input value="102+İzmit+40.7654+29.9187" />'
        )
        self.assertEqual([stop["stop_id"] for stop in stops], ["101", "102"])
        self.assertAlmostEqual(stops[1]["lat"], 40.7654)

    def test_schedule_parser_reads_weekday_weekend_and_notes(self):
        html = (
            "<div>GÜNCELLEME: 12.10.2026 08:30</div>"
            "<table><tbody>"
            "<tr><th>Sıra</th><th>Hafta İçi</th><th>Cumartesi</th><th>Pazar</th></tr>"
            "<tr><td>1</td><td>06:30</td><td>07:10</td><td>08:20</td></tr>"
            "<tr><td>2</td><td>06:50</td><td>07:40</td><td>09:00</td></tr>"
            "</tbody></table>"
            "<table><tbody><tr><th>Not</th><th>Açıklama</th></tr>"
            "<tr><td>1</td><td>Bayram günleri geçerli değildir.</td></tr></tbody></table>"
        )
        schedule = MapTools.parse_schedule(html)
        self.assertEqual(schedule["weekday"], ["06:30", "06:50"])
        self.assertEqual(schedule["saturday"], ["07:10", "07:40"])
        self.assertEqual(schedule["sunday"], ["08:20", "09:00"])
        self.assertEqual(schedule["updated"], "12.10.2026 08:30")
        self.assertIn("Bayram günleri geçerli değildir.", schedule["notes"])

    def test_bus_line_combines_official_stops_and_schedule(self):
        payloads = []
        stops_html = (
            '<input value="101+Derince Merkez+40.7571+29.8153" />'
            '<input value="102+İzmit+40.7654+29.9187" />'
        )
        schedule_html = (
            "<table><tbody><tr><th>Sıra</th><th>Hafta İçi</th><th>Cumartesi</th><th>Pazar</th></tr>"
            "<tr><td>1</td><td>06:30</td><td>07:10</td><td>08:20</td></tr></tbody></table>"
        )

        def router(method, url, kwargs):
            if "searchRouteDirections" in url:
                return FakeResponse({"direct0": "Derince → İzmit", "direct1": "İzmit → Derince"})
            if "searchRouteSchedule" in url:
                self.assertEqual(kwargs["data"]["route_code"], "10")
                return FakeResponse(text=schedule_html)
            if "searchRouteStops" in url:
                return FakeResponse(text=json.dumps(stops_html))
            raise AssertionError(f"beklenmeyen istek: {url}")

        tools, _ = tools_with(router, listener=payloads.append)
        tools._cache_put("line_catalog", [{"code": "10", "label": "Derince - İzmit"}])
        result = tools.run({"action": "bus_line", "line": "10"})

        self.assertIn("Kocaeli 10", result)
        self.assertIn("Derince Merkez → İzmit", result)
        self.assertIn("06:30", result)
        self.assertEqual(payloads[-1]["kind"], "bus_line")
        self.assertEqual(payloads[-1]["stops"][1]["stop_id"], "102")


class MallTests(unittest.TestCase):
    MALL = {
        "lat": "40.7667685",
        "lon": "29.9769875",
        "osm_type": "way",
        "osm_id": "123456",
        "type": "mall",
        "class": "shop",
        "name": "Arasta Park AVM",
        "display_name": "Arasta Park AVM, Akasyalar Caddesi, Yahyakaptan, İzmit, Kocaeli, Türkiye",
        "address": {
            "road": "Akasyalar Caddesi",
            "suburb": "Yahyakaptan",
            "town": "İzmit",
            "state": "Kocaeli",
        },
        "boundingbox": ["40.7658", "40.7677", "29.9758", "29.9782"],
    }
    RELATION_MALL = dict(MALL, osm_type="relation")
    STORES = {"elements": [
        {"type": "node", "id": 1, "lat": 40.7665, "lon": 29.9767, "tags": {
            "name": "Zara", "shop": "clothes", "level": "1",
        }},
        {"type": "node", "id": 2, "lat": 40.7666, "lon": 29.9768, "tags": {
            "name": "Starbucks", "amenity": "cafe", "level": "0",
            "website": "www.starbucks.com.tr", "opening_hours": "09:00-22:00",
        }},
        {"type": "node", "id": 3, "lat": 40.7667, "lon": 29.9769, "tags": {
            "name": "Teknosa", "shop": "electronics", "level": "1",
            "phone": "+90 262 000 00 00", "wheelchair": "yes",
        }},
        {"type": "node", "id": 4, "lat": 40.7665, "lon": 29.9767, "tags": {
            "name": "Zara", "shop": "clothes", "level": "1",
        }},
        {"type": "node", "id": 5, "lat": 40.7668, "lon": 29.9770, "tags": {
            "name": "Arasta Park AVM", "shop": "mall",
        }},
        {"type": "node", "id": 6, "lat": 40.7668, "lon": 29.9770, "tags": {
            "shop": "shoes",
        }},
    ]}
    OSM_XML = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<osm version='0.6'>"
        "<node id='1' lat='40.7665' lon='29.9767'>"
        "<tag k='name' v='Zara'/><tag k='shop' v='clothes'/><tag k='level' v='1'/>"
        "</node>"
        "<node id='2' lat='40.7666' lon='29.9768'>"
        "<tag k='name' v='Starbucks'/><tag k='amenity' v='cafe'/>"
        "</node>"
        "<node id='10' lat='40.7664' lon='29.9766'/>"
        "<node id='11' lat='40.7668' lon='29.9770'/>"
        "<node id='12' lat='40.7668' lon='29.9766'/>"
        "<node id='13' lat='40.7664' lon='29.9770'/>"
        "<way id='20'><nd ref='10'/><nd ref='11'/><nd ref='12'/><nd ref='13'/>"
        "<tag k='name' v='Teknosa'/><tag k='shop' v='electronics'/></way>"
        "<node id='99' lat='41.5000' lon='30.5000'>"
        "<tag k='name' v='Uzak Mağaza'/><tag k='shop' v='clothes'/>"
        "</node>"
        "</osm>"
    )

    def test_mall_lists_stores_under_single_logo_payload(self):
        payloads = []
        nominatim_queries = []
        overpass_queries = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                nominatim_queries.append(kwargs["params"]["q"])
                return FakeResponse([self.RELATION_MALL])
            if "overpass" in url:
                overpass_queries.append(kwargs["data"]["data"])
                return FakeResponse(self.STORES)
            raise AssertionError(url)

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.run({"action": "mall", "query": "Arasta Park AVM'deki mağazalar"})

        self.assertEqual(nominatim_queries, ["Arasta Park AVM"])
        self.assertIn("area(3600123456)", overpass_queries[0])
        self.assertIn("3 mağaza/işletme bulundu", result)
        self.assertIn("Zara", result)
        self.assertIn("Giyim", result)
        self.assertIn("AVM logosuna tıkla", result)

        payload = payloads[-1]
        self.assertEqual(payload["kind"], "mall")
        self.assertEqual(payload["mall"]["name"], "Arasta Park AVM")
        self.assertEqual(
            payload["mall"]["address"], "Akasyalar Caddesi, Yahyakaptan, İzmit"
        )
        self.assertEqual(len(payload["stores"]), 3)
        self.assertNotIn("items", payload)
        stores = {store["name"]: store for store in payload["stores"]}
        self.assertEqual(stores["Zara"]["category"], "Giyim")
        self.assertEqual(stores["Zara"]["level"], "Kat 1")
        self.assertEqual(stores["Starbucks"]["category"], "Kafe")
        self.assertEqual(stores["Teknosa"]["phone"], "+90 262 000 00 00")
        self.assertEqual(stores["Teknosa"]["wheelchair"], "Erişilebilir")

    def test_mall_photo_flows_into_payload(self):
        payloads = []
        stores_with_photo = {"elements": [
            {"type": "node", "id": 1, "lat": 40.7665, "lon": 29.9767, "tags": {
                "name": "Zara", "shop": "clothes", "level": "1",
            }},
            {"type": "node", "id": 5, "lat": 40.7668, "lon": 29.9770, "tags": {
                "name": "Arasta Park AVM", "shop": "mall",
                "wikimedia_commons": "File:Arasta_Park.jpg",
            }},
        ]}

        def router(method, url, kwargs):
            if "nominatim" in url:
                return FakeResponse([self.RELATION_MALL])
            if "overpass" in url:
                return FakeResponse(stores_with_photo)
            raise AssertionError(url)

        tools, _ = tools_with(router, listener=payloads.append)
        tools.run({"action": "mall", "query": "Arasta Park AVM"})

        mall = payloads[-1]["mall"]
        self.assertIn("Special:FilePath/Arasta_Park.jpg", mall["photo"])

    def test_geocode_named_mall_opens_mall_view_without_repeat_search(self):
        payloads = []
        nominatim_calls = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                nominatim_calls.append(kwargs["params"]["q"])
                return FakeResponse([self.MALL])
            if "api.openstreetmap.org" in url:
                return FakeResponse(text=self.OSM_XML)
            raise AssertionError(url)

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.run({"action": "geocode", "query": "Arasta Park AVM"})

        self.assertEqual(nominatim_calls, ["Arasta Park AVM"])
        self.assertEqual(payloads[-1]["kind"], "mall")
        self.assertIn("3 mağaza", result)

    def test_named_mall_nearby_routes_to_mall_view(self):
        payloads = []
        nominatim_queries = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                nominatim_queries.append(kwargs["params"]["q"])
                return FakeResponse([self.MALL])
            if "api.openstreetmap.org" in url:
                return FakeResponse(text=self.OSM_XML)
            raise AssertionError(url)

        tools, _ = tools_with(router, listener=payloads.append)
        tools.run({
            "action": "nearby",
            "query": "Arasta Park AVM'deki mağazalar",
            "radius": 1200,
        })

        self.assertEqual(nominatim_queries, ["Arasta Park AVM"])
        self.assertEqual(payloads[-1]["kind"], "mall")

    def test_mall_store_area_falls_back_to_bounding_radius(self):
        queries = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                return FakeResponse([self.RELATION_MALL])
            if "overpass" in url:
                data = kwargs["data"]["data"]
                queries.append(data)
                return FakeResponse({"elements": []})
            if "api.openstreetmap.org" in url:
                return FakeResponse(text=self.OSM_XML)
            raise AssertionError(url)

        tools, _ = tools_with(router)
        result = tools.mall("Arasta Park AVM")

        self.assertEqual(result["source"], "radius")
        self.assertEqual(len(result["stores"]), 3)
        self.assertTrue(any("area(" in query for query in queries))
        self.assertGreaterEqual(result["mall"]["radius_m"], 120)
        self.assertLessEqual(result["mall"]["radius_m"], 900)

    def test_way_mall_uses_radius_without_area_query(self):
        osm_requests = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                return FakeResponse([self.MALL])
            if "api.openstreetmap.org" in url:
                osm_requests.append(kwargs["params"]["bbox"])
                return FakeResponse(text=self.OSM_XML)
            raise AssertionError(url)

        tools, _ = tools_with(router)
        result = tools.mall("Arasta Park AVM")

        self.assertEqual(result["source"], "radius")
        self.assertEqual(len(osm_requests), 1)
        self.assertEqual(len(result["stores"]), 3)

    def test_relation_area_falls_back_to_osm_api_when_overpass_fails(self):
        payloads = []
        osm_requests = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                return FakeResponse([self.RELATION_MALL])
            if "overpass" in url:
                return requests.ConnectionError("overpass down")
            if "api.openstreetmap.org" in url:
                osm_requests.append(kwargs["params"]["bbox"])
                return FakeResponse(text=self.OSM_XML)
            raise AssertionError(url)

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.mall("Arasta Park AVM")

        self.assertEqual(len(osm_requests), 1)
        self.assertIn(",", osm_requests[0])
        self.assertEqual({store["name"] for store in result["stores"]}, {
            "Zara", "Starbucks", "Teknosa",
        })
        self.assertEqual(result["store_error"], "")
        stores = {store["name"]: store for store in result["stores"]}
        self.assertEqual(stores["Teknosa"]["level"], "")
        self.assertEqual(stores["Teknosa"]["category"], "Elektronik")
        self.assertEqual(payloads[-1]["kind"], "mall")

    def test_store_lookup_falls_back_to_overpass_when_osm_api_fails(self):
        queries = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                return FakeResponse([self.MALL])
            if "api.openstreetmap.org" in url:
                return FakeResponse(text="")
            if "overpass" in url:
                queries.append(kwargs["data"]["data"])
                return FakeResponse(self.STORES)
            raise AssertionError(url)

        tools, _ = tools_with(router)
        result = tools.mall("Arasta Park AVM")

        self.assertEqual(result["store_error"], "")
        self.assertEqual(len(result["stores"]), 3)
        self.assertTrue(queries)
        self.assertFalse(any("area(" in query for query in queries))

    def test_mall_marker_survives_store_service_failure(self):
        payloads = []

        def router(method, url, kwargs):
            if "nominatim" in url:
                return FakeResponse([self.MALL])
            return FakeResponse(None)

        tools, _ = tools_with(router, listener=payloads.append)
        result = tools.run({"action": "mall", "query": "Arasta Park AVM"})

        self.assertIn("Mağaza servisi şu an yanıt vermedi", result)
        payload = payloads[-1]
        self.assertEqual(payload["kind"], "mall")
        self.assertEqual(payload["stores"], [])
        self.assertTrue(payload["store_error"])
        self.assertAlmostEqual(payload["mall"]["lat"], 40.7667685)

    def test_mall_search_rejects_non_mall_place(self):
        park = {
            "lat": "40.7668", "lon": "29.9770", "type": "park", "class": "leisure",
            "name": "Arasta Park",
            "display_name": "Arasta Park, İzmit, Kocaeli",
            "address": {"suburb": "Yahyakaptan", "town": "İzmit", "state": "Kocaeli"},
        }

        tools, _ = tools_with(lambda method, url, kwargs: FakeResponse([park]))
        result = tools.run({"action": "mall", "query": "Arasta Park AVM'deki mağazalar"})

        self.assertIn("AVM/plaza bulunamadı", result)

    def test_mall_query_detection_ignores_generic_avm_lists(self):
        self.assertTrue(MapTools._is_mall_query("Arasta Park AVM'deki mağazalar"))
        self.assertTrue(MapTools._is_mall_query("Arasta Park mağazaları"))
        self.assertFalse(MapTools._is_mall_query("çevredeki AVM'ler"))
        self.assertFalse(MapTools._is_mall_query("AVM"))


class MapSafetyTests(unittest.TestCase):
    def test_map_risk_classification_and_invalid_action(self):
        policy = MagicMock()
        self.assertEqual(classify_action("map", {"action": "route"}, policy), Risk.READ)
        self.assertEqual(classify_action("map", {"action": "geocode"}, policy), Risk.READ)
        self.assertEqual(classify_action("map", {"action": "mall"}, policy), Risk.READ)
        self.assertEqual(classify_action("map", {"action": "set_home"}, policy), Risk.MODIFY)
        with self.assertRaises(ActionForbidden):
            classify_action("map", {"action": "delete_everything"}, policy)

    def test_executor_dispatches_map_action_with_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            services = SafetyServices(
                policy=PathPolicy([root], base=root),
                gate=PermissionGate(),
                audit=AuditLog(root / "data" / "audit.jsonl"),
                undo=UndoStore(root / "data" / "undo"),
                kill=KillSwitch(),
                settings=StubSettings(),
            )
            payloads = []
            executor = ActionExecutor(services, map_listener=payloads.append)
            executor.maps.run = MagicMock(return_value="✅ harita hazır")

            result = executor.execute(PlannedAction("map", {"action": "home"}, "Harita"))

            self.assertEqual(result, "✅ harita hazır")
            executor.maps.run.assert_called_once_with({"action": "home"})

    def test_tool_declaration_and_prompt_route_map_requests(self):
        declarations = {
            item["name"]: item
            for group in TOOL_DECLARATIONS
            for item in group["functionDeclarations"]
        }
        self.assertIn("map", declarations)
        map_tool = declarations["map"]
        self.assertEqual(map_tool["parameters"]["required"], ["action"])
        self.assertIn("bus_stop", map_tool["parameters"]["properties"]["action"]["enum"])
        self.assertIn("mall", map_tool["parameters"]["properties"]["action"]["enum"])
        self.assertIn("map aracını", AI.DEFAULT_SYSTEM_PROMPT)
        self.assertIn("action=mall", AI.DEFAULT_SYSTEM_PROMPT)


class LocalPoiStoreTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = PoiStore(Path(self._temp.name) / "poi.sqlite3")
        self.store.upsert_records([
            {
                "id": "pharmacy-1", "name": "Deva Eczanesi", "kind_raw": "pharmacy",
                "lat": 40.7572, "lon": 29.8152, "phone": "+90 262 111",
            },
            {
                "id": "pharmacy-2", "name": "Uzak Eczane", "kind_raw": "pharmacy",
                "lat": 40.7670, "lon": 29.8250,
            },
            {
                "id": "pharmacy-3", "name": "Kapalı Eczane", "kind_raw": "pharmacy",
                "lat": 40.7573, "lon": 29.8153, "status": "closed",
            },
            {
                "id": "mall-1", "name": "Kipa AVM", "kind_raw": "shopping_mall",
                "lat": 40.7600, "lon": 29.8200,
            },
            {
                "id": "mall-2", "name": "ZENAY Kuyumculuk", "kind_raw": "shopping_mall",
                "lat": 40.7610, "lon": 29.8210,
            },
        ])
        self.store._record_coverage((40.70, 40.82, 29.75, 29.90), 0)

    def test_turkish_fold_and_category_normalization(self):
        self.assertEqual(fold_tr("ŞİŞLİ ĞÜÇÖ İZMİT"), "sisli guco izmit")
        self.assertEqual(ui_kind("shopping_mall"), "mall")
        self.assertEqual(ui_kind("grocery_store"), "supermarket")
        self.assertEqual(ui_kind("doner_kebab_restaurant"), "restaurant")
        self.assertEqual(category_label("pharmacy"), "Eczane")
        self.assertIn("Hırdavat", category_label("hardware_store"))

    def test_search_sorts_by_distance_and_hides_closed_places(self):
        rows = self.store.search_near(40.757, 29.815, 3000, term="eczane", limit=10)
        self.assertEqual([row["name"] for row in rows], ["Deva Eczanesi", "Uzak Eczane"])
        self.assertLess(rows[0]["distance_m"], rows[1]["distance_m"])
        self.assertEqual(rows[0]["phone"], "+90 262 111")
        self.assertEqual(rows[0]["source"], "Overture")

    def test_coverage_gate_blocks_repeat_import(self):
        self.assertTrue(self.store.coverage_contains(40.75, 40.76, 29.81, 29.82))
        self.assertFalse(self.store.coverage_contains(30.0, 30.1, 20.0, 20.1))
        self.assertFalse(
            self.store.ensure_import_async(40.75, 40.76, 29.81, 29.82)
        )

    def test_oversized_import_is_refused_before_network(self):
        with self.assertRaises(ValueError):
            self.store.import_bbox(-10, 10, -10, 10)

    def test_default_store_requires_real_settings_data_dir(self):
        self.assertIsNone(PoiStore.default(StubSettings()))
        holder = type("Holder", (), {"data_dir": self._temp.name})()
        store = PoiStore.default(holder)
        self.assertIsNotNone(store)
        self.assertTrue(str(store.path).endswith("poi.sqlite3"))


class LocalPoiMapIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = PoiStore(Path(self._temp.name) / "poi.sqlite3")
        self.store.upsert_records([
            {
                "id": "pharmacy-1", "name": "Deva Eczanesi", "kind_raw": "pharmacy",
                "lat": 40.7572, "lon": 29.8152, "phone": "+90 262 111",
                "address": "Cumhuriyet Caddesi",
            },
            {
                "id": "mall-1", "name": "Kipa AVM", "kind_raw": "shopping_mall",
                "lat": 40.7600, "lon": 29.8200,
            },
            {
                "id": "mall-2", "name": "ZENAY Kuyumculuk", "kind_raw": "shopping_mall",
                "lat": 40.7610, "lon": 29.8210,
            },
            {
                "id": "mall-3", "name": "Turkcell İzmit Plaza", "kind_raw": "shopping_mall",
                "lat": 40.7620, "lon": 29.8220,
            },
        ])
        self.store._record_coverage((40.70, 40.82, 29.75, 29.90), 0)

    def test_nearby_merges_local_phone_with_osm_hours(self):
        def router(method, url, kwargs):
            self.assertIn("overpass", url)
            return FakeResponse({"elements": [{
                "type": "node", "id": 1, "lat": 40.7572, "lon": 29.8152,
                "tags": {
                    "name": "Deva Eczanesi", "amenity": "pharmacy",
                    "opening_hours": "09:00-19:00",
                    "website": "https://deva.example",
                },
            }]})

        tools, _ = tools_with(router)
        tools._store = self.store
        result = tools.nearby("eczane", radius=2000, retries=0)

        self.assertEqual(result["items"][0]["name"], "Deva Eczanesi")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["phone"], "+90 262 111")
        self.assertEqual(item["open"], "09:00-19:00")
        self.assertEqual(item["website"], "https://deva.example")
        self.assertEqual(item["source"], "Overture + OpenStreetMap")
        self.assertIn("Overture", item["source_label"])

    def test_nearby_falls_back_to_local_when_overpass_is_down(self):
        def router(method, url, kwargs):
            raise requests.ConnectionError("overpass down")

        tools, _ = tools_with(router)
        tools._store = self.store
        result = tools.nearby("eczane", radius=2000, retries=0)

        self.assertEqual(result["items"][0]["name"], "Deva Eczanesi")
        self.assertEqual(result["items"][0]["source"], "Overture")

    def test_click_nearby_uses_local_when_osm_api_is_down(self):
        calls = []

        def router(method, url, kwargs):
            calls.append(url)
            raise requests.ConnectionError("osm down")

        tools, _ = tools_with(router)
        tools._store = self.store
        result = tools.click_nearby(40.757, 29.815, 15)

        self.assertEqual(result["items"][0]["name"], "Deva Eczanesi")
        self.assertEqual(len(calls), 1)

    def test_avm_search_filters_noisy_overture_mall_entries(self):
        def router(method, url, kwargs):
            return FakeResponse({"elements": []})

        tools, _ = tools_with(router)
        tools._store = self.store
        result = tools.nearby("avm", radius=3000, retries=0)

        names = [item["name"] for item in result["items"]]
        self.assertIn("Kipa AVM", names)
        self.assertNotIn("ZENAY Kuyumculuk", names)
        self.assertNotIn("Turkcell İzmit Plaza", names)


class NameInferenceTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = PoiStore(Path(self._temp.name) / "poi.sqlite3")
        self.store.upsert_records([
            {
                "id": "cemetery-1", "name": "Tavşantepe Mezarlığı",
                "kind_raw": "pharmacy", "lat": 40.7573, "lon": 29.8153,
            },
            {
                "id": "pharmacy-1", "name": "Etem Eczanesi",
                "kind_raw": "christian_place_of_worship",
                "lat": 40.7572, "lon": 29.8152,
            },
        ])
        self.store._record_coverage((40.70, 40.82, 29.75, 29.90), 0)

    def test_name_rules_correct_misleading_overture_taxonomy(self):
        self.assertEqual(infer_name_category("Tavşantepe Mezarlığı"), ("place", "Mezarlık"))
        rows = self.store.search_near(40.757, 29.815, 2000, term="eczane", limit=10)
        names = [row["name"] for row in rows]
        self.assertIn("Etem Eczanesi", names)
        self.assertNotIn("Tavşantepe Mezarlığı", names)

    def test_reclassify_repairs_rows_written_before_rules(self):
        with self.store._db() as connection:
            connection.execute(
                "UPDATE places SET kind = 'pharmacy', category = 'Eczane', "
                "category_fold = 'eczane' WHERE id = 'cemetery-1'"
            )
            connection.commit()
        self.assertIn(
            "Tavşantepe Mezarlığı",
            [row["name"] for row in self.store.search_near(
                40.757, 29.815, 2000, term="eczane", limit=10
            )],
        )

        self.assertGreaterEqual(self.store.reclassify_names(), 1)

        self.assertNotIn(
            "Tavşantepe Mezarlığı",
            [row["name"] for row in self.store.search_near(
                40.757, 29.815, 2000, term="eczane", limit=10
            )],
        )


if __name__ == "__main__":
    unittest.main()
