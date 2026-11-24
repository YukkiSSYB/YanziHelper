"""Paint the artwork, navigation wash and translucent configuration cards."""

from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps


# Position of the face in each original, in fractions of the image width/height.
ARTWORK = {
    "yunchang": ("yunshang.png", 0.495, 0.29),
    "feitian": ("feitian.png", 0.408, 0.23),
}
RAIL_WASH = (
    (0.00, 246), (0.10, 242), (0.19, 230), (0.25, 170), (0.31, 35),
    (0.37, 5), (0.79, 5), (0.88, 40), (0.95, 208), (1.00, 243),
)


def _rail_mask(width, height):
    levels = []
    for (_, start_alpha), (end, end_alpha) in zip(RAIL_WASH, RAIL_WASH[1:]):
        length = round(end * height) - len(levels)
        levels.extend(round(start_alpha + (end_alpha - start_alpha) * i / length)
                      for i in range(length))
    return Image.frombytes("L", (1, height), bytes(levels)).resize((width, height))


class Backdrops:
    def __init__(self, asset_dir: Path):
        self.asset_dir = Path(asset_dir)
        self.originals = {}
        self.scene_key = None
        self.scene = None

    def _landscape(self, school, size, rail):
        filename, face_x, face_y = ARTWORK[school]
        if school not in self.originals:
            with Image.open(self.asset_dir / filename) as original:
                self.originals[school] = original.convert("RGB")
        original = self.originals[school]
        width, height = size
        source_width, source_height = original.size
        face_x *= source_width
        face_y *= source_height
        target_x, target_y = rail * 0.50, height * 0.36
        natural_scale = max(height / source_height, target_y / face_y)
        fit_right = (width - target_x) / (source_width - face_x)
        scale = max(natural_scale, min(fit_right, natural_scale * 1.23))
        left = max(0.0, face_x - target_x / scale)
        top = max(0.0, min(face_y - target_y / scale, source_height - height / scale))
        drawn = min(width, max(1, int((source_width - left) * scale)))
        crop = (left, top, min(source_width, left + drawn / scale),
                min(source_height, top + height / scale))
        scene = original.resize((drawn, height), Image.Resampling.LANCZOS, box=crop)
        if drawn == width:
            return scene

        # Wide windows can exhaust the scenery to the right of the anchored face.
        # Reflect only the outer landscape strip. Blur the extension progressively
        # so its edge meets the full-resolution original at the same column.
        gap = width - drawn
        strip_width = min(drawn, gap, max(1, round(source_width * scale * 0.18)))
        strip = ImageOps.mirror(scene.crop((drawn - strip_width, 0, drawn, height)))
        extension = strip.resize((gap, height), Image.Resampling.BICUBIC)
        blur = extension.filter(ImageFilter.GaussianBlur(radius=max(2, height / 110)))
        fade = Image.linear_gradient("L").rotate(90).resize((gap, height))
        extended = Image.new("RGB", size)
        extended.paste(scene)
        extended.paste(Image.composite(blur, extension, fade), (drawn, 0))
        return extended

    def render(self, school, size, rail, panels, accent):
        """Return RGB pixels. Panel rectangles and their corner radii are in pixels."""
        width, height = size
        rail = min(width, max(0, int(rail)))
        key = (school, width, height, rail, accent)
        if key != self.scene_key:
            scene = self._landscape(school, size, rail)
            ink = ImageColor.getrgb(accent)
            mist = tuple(round(250 * 0.95 + channel * 0.05) for channel in ink)
            if rail:
                scene.paste(mist, (0, 0, rail, height), _rail_mask(rail, height))
            if rail < width:
                draw = ImageDraw.Draw(scene, "RGBA")
                draw.rectangle((rail, 0, width - 1, height - 1), fill=(*mist, 93))
                draw.line((rail, 0, rail, height - 1), fill=(255, 255, 255, 175))
            self.scene_key, self.scene = key, scene

        frame = self.scene.copy()
        draw = ImageDraw.Draw(frame, "RGBA")
        for left, top, right, bottom, radius, opacity in panels:
            if right <= left or bottom <= top:
                continue
            alpha = round(255 * opacity if opacity <= 1 else opacity)
            draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=radius,
                                   fill=(255, 254, 255, min(255, max(0, alpha))),
                                   outline=(255, 255, 255, 182))
        return frame
