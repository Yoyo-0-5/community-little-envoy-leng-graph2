import base64
import binascii
from io import BytesIO

from PIL import ExifTags, Image, UnidentifiedImageError


class ImageMetadataError(ValueError):
    """An image payload cannot be decoded for metadata extraction."""


def _image_bytes(value: str) -> bytes:
    payload = value
    if value.startswith("data:"):
        header, separator, payload = value.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ImageMetadataError("照片必須使用 base64 data URI。")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageMetadataError("照片的 base64 資料格式不正確。") from exc


def _degrees(value) -> float:
    degrees, minutes, seconds = value

    def rational(part) -> float:
        if isinstance(part, tuple):
            numerator, denominator = part
            return numerator / denominator
        return float(part)

    return rational(degrees) + rational(minutes) / 60 + rational(seconds) / 3600


def _reference(value) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii").upper()
    return str(value).upper()


def _gps_location(image_bytes: bytes) -> dict | None:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            exif = image.getexif()
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageMetadataError("無法讀取照片，請確認檔案是有效的圖片。") from exc

    if not gps:
        return None
    tags = {ExifTags.GPSTAGS.get(tag, tag): value for tag, value in gps.items()}
    latitude = tags.get("GPSLatitude")
    longitude = tags.get("GPSLongitude")
    latitude_ref = tags.get("GPSLatitudeRef")
    longitude_ref = tags.get("GPSLongitudeRef")
    if not all((latitude, longitude, latitude_ref, longitude_ref)):
        return None

    try:
        lat = _degrees(latitude)
        lng = _degrees(longitude)
        if _reference(latitude_ref) == "S":
            lat = -lat
        if _reference(longitude_ref) == "W":
            lng = -lng
    except (TypeError, ValueError, UnicodeDecodeError, ZeroDivisionError) as exc:
        raise ImageMetadataError("照片中的 GPS 資訊格式不正確。") from exc

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ImageMetadataError("照片中的 GPS 座標超出有效範圍。")
    return {"lat": lat, "lng": lng}


def extract_image_location(images: list[str]) -> dict | None:
    """Return the first embedded GPS coordinate, if any."""
    for index, value in enumerate(images):
        if not isinstance(value, str):
            raise ImageMetadataError("照片資料必須是 base64 字串。")
        location = _gps_location(_image_bytes(value))
        if location:
            return {**location, "source": "exif", "image_index": index}
    return None
