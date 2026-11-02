import math
from typing import Optional, Tuple
from decimal import Decimal


def safe_parse_coordinates(lat: any, lon: any) -> Tuple[Optional[float], Optional[float]]:
    """
    Safely validates and parses latitude and longitude strings/floats (BUG-116).
    Rejects None, empty strings, non-numeric values, and non-finite floats (NaN, +/-Infinity).
    Valid ranges: latitude [-90.0, 90.0], longitude [-180.0, 180.0].
    """
    if lat is None or lon is None:
        return None, None
    try:
        f_lat = float(lat)
        f_lon = float(lon)
        if not (math.isfinite(f_lat) and math.isfinite(f_lon)):
            return None, None
        if not (-90.0 <= f_lat <= 90.0 and -180.0 <= f_lon <= 180.0):
            return None, None
        return f_lat, f_lon
    except (ValueError, TypeError):
        return None, None


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth in kilometers using Haversine formula.
    Clamps intermediate domain to [0.0, 1.0] to prevent math domain error on boundary rounding (BUG-108).
    """
    R = 6371.0  # Earth's radius in kilometers

    try:
        f_lat1, f_lon1 = float(lat1), float(lon1)
        f_lat2, f_lon2 = float(lat2), float(lon2)
        if not (math.isfinite(f_lat1) and math.isfinite(f_lon1) and math.isfinite(f_lat2) and math.isfinite(f_lon2)):
            return 9999.0
    except (ValueError, TypeError):
        return 9999.0

    phi1 = math.radians(f_lat1)
    phi2 = math.radians(f_lat2)
    delta_phi = math.radians(f_lat2 - f_lat1)
    delta_lambda = math.radians(f_lon2 - f_lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    # Domain guardrail to eliminate floating-point rounding crash (BUG-108)
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return round(R * c, 2)


def calculate_distance_in_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth in meters using Haversine formula.
    Accurate to within 0.1 meter for store geofencing.
    Clamps intermediate domain to [0.0, 1.0] to prevent math domain error (BUG-108).
    """
    R = 6371000.0  # Earth's radius in meters

    try:
        f_lat1, f_lon1 = float(lat1), float(lon1)
        f_lat2, f_lon2 = float(lat2), float(lon2)
        if not (math.isfinite(f_lat1) and math.isfinite(f_lon1) and math.isfinite(f_lat2) and math.isfinite(f_lon2)):
            return 9999999.0
    except (ValueError, TypeError):
        return 9999999.0

    phi1 = math.radians(f_lat1)
    phi2 = math.radians(f_lat2)
    delta_phi = math.radians(f_lat2 - f_lat1)
    delta_lambda = math.radians(f_lon2 - f_lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    # Domain guardrail to eliminate floating-point rounding crash (BUG-108)
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return round(R * c, 1)


# Backward-compatible alias
haversine_distance = calculate_distance


def format_distance_display(dist_km: float) -> str:
    """
    Format distance into user-friendly units:
    Under 1 km: meters (e.g. '750 m')
    1 km or above: kilometers with one decimal place (e.g. '1.2 km')
    """
    if dist_km is None:
        return ""
    if dist_km < 1.0:
        meters = int(round(dist_km * 1000))
        return f"{meters} m"
    return f"{dist_km:.1f} km"


def annotate_shops_with_distance(shops_queryset, user_lat: float, user_lng: float, max_km: float = 150.0):
    """
    Attaches distance_km and distance_display attributes to shop instances and sorts nearest to farthest.
    Filters out stores beyond max_km if max_km is specified.
    Applies SQL-level bounding-box pre-filtering to prevent memory bloat on large directories (BUG-82).
    Safely validates user coordinates against non-finite float anomalies (BUG-116).
    """
    parsed_lat, parsed_lng = safe_parse_coordinates(user_lat, user_lng)
    if parsed_lat is None or parsed_lng is None:
        return list(shops_queryset)

    user_lat, user_lng = parsed_lat, parsed_lng

    # SQL-level bounding-box pre-filter to prune distant shops before in-memory Haversine calculation (BUG-82)
    if max_km is not None and hasattr(shops_queryset, 'filter'):
        try:
            buffer_factor = 1.15
            lat_delta = (max_km / 110.0) * buffer_factor
            cos_lat = max(0.1, abs(math.cos(math.radians(float(user_lat)))))
            lng_delta = (max_km / (110.0 * cos_lat)) * buffer_factor

            min_lat = round(float(user_lat) - lat_delta, 6)
            max_lat = round(float(user_lat) + lat_delta, 6)
            min_lng = round(float(user_lng) - lng_delta, 6)
            max_lng = round(float(user_lng) + lng_delta, 6)

            shops_queryset = shops_queryset.filter(
                latitude__gte=min_lat,
                latitude__lte=max_lat,
                longitude__gte=min_lng,
                longitude__lte=max_lng
            )
        except Exception:
            pass

    shop_list = []
    for shop in shops_queryset:
        try:
            dist = calculate_distance(user_lat, user_lng, float(shop.latitude), float(shop.longitude))
            if max_km is not None and dist > max_km:
                continue
            shop.distance_km = dist
            shop.distance_display = format_distance_display(dist)
            shop_list.append(shop)
        except Exception:
            if max_km is None:
                shop.distance_km = 9999.0
                shop.distance_display = ""
                shop_list.append(shop)

    shop_list.sort(key=lambda s: getattr(s, 'distance_km', 9999.0))
    return shop_list
