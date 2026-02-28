"""Fishing Forecast Service.

Combines solunar data (moon/sun phases, feeding periods) with weather data
to calculate a fishing score and forecast.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)


class ForecastService:
    """Service for fishing forecast calculations."""

    # Cache settings
    CACHE_PREFIX = "forecast"
    CACHE_TTL = 3600  # 1 hour

    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def get_redis(self) -> redis.Redis:
        """Get Redis client."""
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def get_http_client(self) -> httpx.AsyncClient:
        """Get HTTP client for API calls."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def close(self):
        """Close connections."""
        if self._redis:
            await self._redis.close()
        if self._http_client:
            await self._http_client.aclose()

    def _cache_key(self, lat: float, lng: float, date: str) -> str:
        """Build cache key with rounded coordinates."""
        # Round to 2 decimal places (~1km precision)
        lat_rounded = round(lat, 2)
        lng_rounded = round(lng, 2)
        return f"{self.CACHE_PREFIX}:{lat_rounded}:{lng_rounded}:{date}"

    async def _get_cached(self, key: str) -> Optional[dict]:
        """Get cached forecast."""
        client = await self.get_redis()
        data = await client.get(key)
        if data:
            return json.loads(data)
        return None

    async def _set_cached(self, key: str, data: dict):
        """Cache forecast data."""
        client = await self.get_redis()
        await client.setex(key, self.CACHE_TTL, json.dumps(data, default=str))

    # === Solunar API ===

    async def fetch_solunar(
        self, lat: float, lng: float, date: str, timezone: int = 2
    ) -> Optional[dict]:
        """
        Fetch solunar data from api.solunar.org.

        Args:
            lat: Latitude
            lng: Longitude
            date: Date in YYYYMMDD format
            timezone: Timezone offset (default 2 for Romania/EET)

        Returns:
            Solunar data dict or None on error
        """
        url = f"https://api.solunar.org/solunar/{lat},{lng},{date},{timezone}"

        try:
            client = await self.get_http_client()
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            logger.warning(f"Solunar API unavailable: status={status} url={url} err={e}")
            return None
        except Exception as e:
            logger.warning(f"Solunar fetch failed: url={url} err={e}")
            return None

    # === OpenWeatherMap API ===

    async def fetch_weather(self, lat: float, lng: float) -> Optional[dict]:
        """
        Fetch current weather from OpenWeatherMap.

        Returns:
            Weather data dict or None on error
        """
        settings = get_settings()
        if not settings.openweathermap_api_key:
            logger.warning("OpenWeatherMap API key not configured")
            return None

        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "lat": lat,
            "lon": lng,
            "appid": settings.openweathermap_api_key,
            "units": "metric",
        }

        try:
            client = await self.get_http_client()
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"OpenWeatherMap API error: {e}")
            return None
        except Exception as e:
            logger.error(f"Weather fetch error: {e}")
            return None

    async def fetch_weather_forecast(self, lat: float, lng: float) -> Optional[dict]:
        """
        Fetch 5-day weather forecast from OpenWeatherMap.

        Returns:
            Forecast data dict or None on error
        """
        settings = get_settings()
        if not settings.openweathermap_api_key:
            return None

        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {
            "lat": lat,
            "lon": lng,
            "appid": settings.openweathermap_api_key,
            "units": "metric",
        }

        try:
            client = await self.get_http_client()
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"OpenWeatherMap Forecast API error: {e}")
            return None
        except Exception as e:
            logger.error(f"Weather forecast fetch error: {e}")
            return None

    # === Fishing Score Algorithm ===

    def calculate_fishing_score(
        self,
        solunar: dict,
        weather: Optional[dict],
        current_time: datetime,
    ) -> int:
        """
        Calculate fishing score 0-100.

        Factors:
        - Solunar period: +25 (major), +15 (minor)
        - Moon phase: +10 (new/full moon)
        - Barometric pressure: +15 (falling), -5 (rising)
        - Wind speed: +10 (light), -10 (strong)
        - Cloud cover: +5 (overcast)
        - Temperature: -10 (extreme hot/cold)
        """
        score = 50  # Base score

        # Solunar periods (+25 max)
        if self._is_major_period(current_time, solunar):
            score += 25
        elif self._is_minor_period(current_time, solunar):
            score += 15

        # Moon phase (+10 max)
        moon_phase = solunar.get("moonPhase", "").lower()
        if "new" in moon_phase or "full" in moon_phase:
            score += 10
        elif "quarter" in moon_phase:
            score += 5

        # Day rating from solunar (+5 max)
        # Clamp to 0-4 (Solunar API sometimes returns invalid values)
        day_rating = min(4, max(0, int(solunar.get("dayRating", 2))))
        if day_rating == 4:
            score += 5
        elif day_rating == 3:
            score += 3

        if weather:
            # Barometric pressure (+15 max)
            pressure = weather.get("main", {}).get("pressure", 1013)
            # Normal is ~1013 hPa, falling is good for fishing
            if pressure < 1010:
                score += 15  # Low/falling pressure
            elif pressure < 1015:
                score += 5  # Normal
            elif pressure > 1020:
                score -= 5  # High/rising pressure

            # Wind speed (+10 max)
            wind_speed = weather.get("wind", {}).get("speed", 3)
            if wind_speed < 3:
                score += 10  # Light breeze
            elif wind_speed < 6:
                score += 5  # Moderate
            elif wind_speed > 10:
                score -= 10  # Strong wind

            # Cloud cover (+5 max)
            clouds = weather.get("clouds", {}).get("all", 50)
            if clouds > 50:
                score += 5  # Overcast often better

            # Temperature extremes (-10 max)
            temp = weather.get("main", {}).get("temp", 15)
            if temp < 5 or temp > 30:
                score -= 10
            elif temp < 10 or temp > 25:
                score -= 5

        return max(0, min(100, score))

    def _parse_time(self, time_str: str, base_date: datetime) -> datetime:
        """Parse time string (HH:MM) to datetime."""
        if not time_str or time_str == "--":
            return base_date
        try:
            hour, minute = map(int, time_str.split(":"))
            return base_date.replace(hour=hour, minute=minute)
        except (ValueError, AttributeError):
            return base_date

    def _is_major_period(self, current: datetime, solunar: dict) -> bool:
        """Check if current time is in a major solunar period."""
        today = current.replace(hour=0, minute=0, second=0, microsecond=0)

        for i in (1, 2):
            start_str = solunar.get(f"major{i}Start", "")
            stop_str = solunar.get(f"major{i}Stop", "")

            if start_str and stop_str and start_str != "--":
                start = self._parse_time(start_str, today)
                stop = self._parse_time(stop_str, today)

                # Handle overnight periods
                if stop < start:
                    stop += timedelta(days=1)

                if start <= current <= stop:
                    return True

        return False

    def _is_minor_period(self, current: datetime, solunar: dict) -> bool:
        """Check if current time is in a minor solunar period."""
        today = current.replace(hour=0, minute=0, second=0, microsecond=0)

        for i in (1, 2):
            start_str = solunar.get(f"minor{i}Start", "")
            stop_str = solunar.get(f"minor{i}Stop", "")

            if start_str and stop_str and start_str != "--":
                start = self._parse_time(start_str, today)
                stop = self._parse_time(stop_str, today)

                if start <= current <= stop:
                    return True

        return False

    def get_score_label(self, score: int) -> str:
        """Get human-readable label for score."""
        if score >= 80:
            return "Excellent"
        elif score >= 60:
            return "Good"
        elif score >= 40:
            return "Fair"
        elif score >= 20:
            return "Poor"
        else:
            return "Bad"

    def get_pressure_trend(self, pressure: float) -> str:
        """Get pressure trend description."""
        if pressure < 1010:
            return "falling"
        elif pressure > 1018:
            return "rising"
        else:
            return "steady"

    def get_wind_direction(self, degrees: int) -> str:
        """Convert wind degrees to compass direction."""
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        index = round(degrees / 45) % 8
        return directions[index]

    def _format_illumination(self, value) -> str:
        """Format moon illumination as percentage string."""
        if value is None or value == "":
            return ""
        if isinstance(value, (int, float)):
            return f"{int(value * 100)}%"
        return str(value)

    # === Main Forecast Method ===

    async def get_forecast(
        self,
        lat: float,
        lng: float,
        days: int = 1,
        include_hourly: bool = False,
        timezone: int = 2,
    ) -> dict:
        """
        Get fishing forecast for a location.

        Args:
            lat: Latitude
            lng: Longitude
            days: Number of days (1-5)
            include_hourly: Include hourly breakdown (Pro feature)
            timezone: Timezone offset

        Returns:
            Forecast data dict
        """
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")

        # Check cache - always store full response and filter on return
        cache_key = self._cache_key(lat, lng, date_str)
        cached = await self._get_cached(cache_key)
        if cached:
            # Return a copy to avoid modifying cached data
            result = dict(cached)
            # Filter Pro features if needed
            if not include_hourly:
                result.pop("hourly_forecast", None)
            if days == 1:
                result.pop("daily_forecast", None)
            return result

        # Fetch data from APIs
        solunar = await self.fetch_solunar(lat, lng, date_str, timezone)
        weather = await self.fetch_weather(lat, lng)

        if not solunar:
            # Solunar failed - still try to return weather data
            response = {
                "current_score": 50,
                "current_label": "Fair",
                "sun_rise": None,
                "sun_set": None,
                "moon_rise": None,
                "moon_set": None,
                "moon_phase": None,
                "moon_illumination": None,
                "day_rating": None,
                "major_periods": [],
                "minor_periods": [],
                "error": "Unable to fetch solunar data - showing weather only",
            }

            # Add weather data if available
            if weather:
                main = weather.get("main", {})
                wind = weather.get("wind", {})
                clouds = weather.get("clouds", {})

                response.update({
                    "temperature": main.get("temp"),
                    "feels_like": main.get("feels_like"),
                    "humidity": main.get("humidity"),
                    "pressure": main.get("pressure"),
                    "pressure_trend": self.get_pressure_trend(main.get("pressure", 1013)),
                    "wind_speed": wind.get("speed"),
                    "wind_direction": self.get_wind_direction(wind.get("deg", 0)),
                    "clouds": clouds.get("all"),
                    "weather_description": weather.get("weather", [{}])[0].get("description", ""),
                })

                # Calculate a basic score from weather only
                score = 50
                pressure = main.get("pressure", 1013)
                if pressure < 1010:
                    score += 10
                wind_speed = wind.get("speed", 3)
                if wind_speed < 3:
                    score += 5
                elif wind_speed > 10:
                    score -= 10
                temp = main.get("temp", 15)
                if temp < 5 or temp > 30:
                    score -= 10
                response["current_score"] = max(0, min(100, score))
                response["current_label"] = self.get_score_label(response["current_score"])

            return response

        # Calculate current score
        current_score = self.calculate_fishing_score(solunar, weather, now)

        # Build response
        response = {
            "current_score": current_score,
            "current_label": self.get_score_label(current_score),
            # Solunar data
            "sun_rise": solunar.get("sunRise", ""),
            "sun_set": solunar.get("sunSet", ""),
            "moon_rise": solunar.get("moonRise", ""),
            "moon_set": solunar.get("moonSet", ""),
            "moon_phase": solunar.get("moonPhase", ""),
            "moon_illumination": self._format_illumination(solunar.get("moonIllumination", "")),
            # Clamp day_rating to 0-4 (Solunar API sometimes returns invalid values)
            "day_rating": min(4, max(0, int(solunar.get("dayRating", 2)))),
            # Major/minor periods
            "major_periods": self._extract_periods(solunar, "major"),
            "minor_periods": self._extract_periods(solunar, "minor"),
        }

        # Add weather data
        if weather:
            main = weather.get("main", {})
            wind = weather.get("wind", {})
            clouds = weather.get("clouds", {})

            response.update({
                "temperature": main.get("temp"),
                "feels_like": main.get("feels_like"),
                "humidity": main.get("humidity"),
                "pressure": main.get("pressure"),
                "pressure_trend": self.get_pressure_trend(main.get("pressure", 1013)),
                "wind_speed": wind.get("speed"),
                "wind_direction": self.get_wind_direction(wind.get("deg", 0)),
                "clouds": clouds.get("all"),
                "weather_description": weather.get("weather", [{}])[0].get("description", ""),
            })

        # Always generate hourly forecast for caching (filter on return)
        response["hourly_forecast"] = self._generate_hourly_forecast(
            solunar, weather, now
        )

        # Always generate daily forecast for caching (5 days max)
        response["daily_forecast"] = await self._generate_daily_forecast(
            lat, lng, 5, timezone
        )

        # Cache the full response with all data
        await self._set_cached(cache_key, response)

        # Filter Pro features if needed before returning
        if not include_hourly:
            response.pop("hourly_forecast", None)
        if days == 1:
            response.pop("daily_forecast", None)

        return response

    def _extract_periods(self, solunar: dict, period_type: str) -> list:
        """Extract major or minor periods from solunar data."""
        periods = []
        for i in (1, 2):
            start = solunar.get(f"{period_type}{i}Start", "")
            stop = solunar.get(f"{period_type}{i}Stop", "")
            if start and stop and start != "--":
                periods.append({
                    "start": start,
                    "end": stop,
                    "type": period_type,
                })
        return periods

    def _generate_hourly_forecast(
        self, solunar: dict, weather: Optional[dict], now: datetime
    ) -> list:
        """Generate hourly fishing scores for the next 24 hours."""
        hourly = []
        base_temp = weather.get("main", {}).get("temp", 15) if weather else 15
        base_wind = weather.get("wind", {}).get("speed", 3) if weather else 3

        for hour_offset in range(24):
            hour_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour_offset)
            hour = hour_time.hour

            # Simulate weather variation (basic)
            temp_variation = -2 + (hour - 12) * 0.3  # Cooler at night
            simulated_weather = {
                "main": {
                    "temp": base_temp + temp_variation,
                    "pressure": weather.get("main", {}).get("pressure", 1013) if weather else 1013,
                },
                "wind": {"speed": base_wind * (0.8 + (abs(hour - 12) * 0.02))},
                "clouds": {"all": weather.get("clouds", {}).get("all", 50) if weather else 50},
            }

            score = self.calculate_fishing_score(solunar, simulated_weather, hour_time)

            hourly.append({
                "hour": hour,
                "score": score,
                "temperature": round(base_temp + temp_variation, 1),
                "is_major_period": self._is_major_period(hour_time, solunar),
                "is_minor_period": self._is_minor_period(hour_time, solunar),
            })

        return hourly

    async def _generate_daily_forecast(
        self, lat: float, lng: float, days: int, timezone: int
    ) -> list:
        """Generate daily forecast for multiple days."""
        daily = []
        now = datetime.now()

        for day_offset in range(min(days, 5)):
            day = now + timedelta(days=day_offset)
            date_str = day.strftime("%Y%m%d")

            solunar = await self.fetch_solunar(lat, lng, date_str, timezone)
            if not solunar:
                continue

            # Calculate best score for the day (during major periods)
            major_periods = self._extract_periods(solunar, "major")
            best_time = major_periods[0]["start"] if major_periods else "10:00"

            # Estimate score (without real-time weather for future days)
            base_score = 50
            # Clamp day_rating to 0-4 (Solunar API sometimes returns invalid values)
            day_rating = min(4, max(0, int(solunar.get("dayRating", 2))))
            base_score += day_rating * 5  # +5 to +20 based on day rating

            moon_phase = solunar.get("moonPhase", "").lower()
            if "new" in moon_phase or "full" in moon_phase:
                base_score += 10

            daily.append({
                "date": day.strftime("%Y-%m-%d"),
                "day_name": day.strftime("%A"),
                "day_rating": day_rating,
                "best_score": min(100, base_score + 15),  # Assume good conditions
                "best_time": best_time,
                "moon_phase": solunar.get("moonPhase", ""),
                "major_periods": major_periods,
            })

        return daily


# Global singleton
forecast_service = ForecastService()
