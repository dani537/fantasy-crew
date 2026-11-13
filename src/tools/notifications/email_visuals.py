"""
Dynamic Animated GIF Visual Generators for Executive HTML Emails (`email_visuals.py`)
===================================================================================
Generates lightweight, crisp, looping animated GIFs (`hero_banner` & `tactical_pitch`)
embedded inline via MIME `Content-ID` (`cid:`) so animations render natively inside
Gmail (Web & Mobile), Apple Mail, Outlook, and Thunderbird without external URLs
or stripped CSS `@keyframes`.

Gracefully returns `None` if `Pillow` is unavailable in minimal environments.
"""

from __future__ import annotations

import io
import math
import os
from typing import Any, Dict, List, Optional, Tuple


_LINE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "GK": (245, 185, 66),   # Gold
    "DF": (56, 189, 248),   # Cyan
    "MF": (16, 185, 129),   # Emerald
    "FW": (251, 113, 133),  # Coral-Rose
}

_POSTURE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "PROTECT_LEAD": (16, 185, 129),
    "TACTICAL_PRESS": (56, 189, 248),
    "ASYMMETRIC_CHASER": (251, 146, 60),
    "EARLY_SEASON_CALIBRATION": (129, 140, 248),
}


def _find_ttf(bold: bool = False, mono: bool = False) -> Optional[str]:
    candidates: List[str] = []
    try:
        import matplotlib
        ttf_dir = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
        if mono:
            candidates.append(os.path.join(ttf_dir, "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"))
        candidates.append(os.path.join(ttf_dir, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"))
    except Exception:
        pass
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if mono else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ])
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def _load_font(size: int, bold: bool = False, mono: bool = False):
    from PIL import ImageFont
    path = _find_ttf(bold=bold, mono=mono)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _blend_rgb(bg: Tuple[int, int, int], fg: Tuple[int, int, int], alpha: float) -> Tuple[int, int, int, int]:
    """Computes solid opaque RGBA color blending `fg` over `bg` without punching alpha holes."""
    return (
        int(bg[0] * (1.0 - alpha) + fg[0] * alpha),
        int(bg[1] * (1.0 - alpha) + fg[1] * alpha),
        int(bg[2] * (1.0 - alpha) + fg[2] * alpha),
        255,
    )


def _fit_text(draw, text: str, font, max_w: float) -> str:
    txt = str(text or "").strip()
    if not txt:
        return ""
    if draw.textlength(txt, font=font) <= max_w:
        return txt
    while len(txt) > 2 and draw.textlength(txt + "…", font=font) > max_w:
        txt = txt[:-1]
    return txt.rstrip() + "…"


def _compact_player_name(draw, full_name: str, font, max_w: float) -> str:
    """Abbreviates `First Last` to `F. Last` cleanly before falling back to ellipsis."""
    name = str(full_name or "").strip()
    if not name:
        return ""
    if draw.textlength(name, font=font) <= max_w:
        return name
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        short = f"{parts[0][0]}. {' '.join(parts[1:])}"
        if draw.textlength(short, font=font) <= max_w:
            return short
        short_last = f"{parts[0][0]}. {parts[-1]}"
        if draw.textlength(short_last, font=font) <= max_w:
            return short_last
    return _fit_text(draw, name, font, max_w)


def _draw_trophy(draw, cx: int, cy: int, scale: float, gold: Tuple[int, int, int, int]) -> None:
    """Draws a crisp vector championship trophy icon inside the crest."""
    s = scale
    draw.polygon(
        [
            (cx - 12 * s, cy - 13 * s),
            (cx + 12 * s, cy - 13 * s),
            (cx + 8 * s, cy + 2 * s),
            (cx - 8 * s, cy + 2 * s),
        ],
        fill=gold,
    )
    draw.arc(
        [cx - 19 * s, cy - 11 * s, cx - 8 * s, cy - 1 * s],
        start=90, end=270, fill=gold, width=max(2, int(2.5 * s)),
    )
    draw.arc(
        [cx + 8 * s, cy - 11 * s, cx + 19 * s, cy - 1 * s],
        start=270, end=90, fill=gold, width=max(2, int(2.5 * s)),
    )
    draw.rectangle([cx - 3 * s, cy + 2 * s, cx + 3 * s, cy + 10 * s], fill=gold)
    draw.rounded_rectangle(
        [cx - 10 * s, cy + 9 * s, cx + 10 * s, cy + 14 * s],
        radius=max(2, int(2 * s)),
        fill=gold,
    )


def _add_soft_glow(base_rgba, cx: int, cy: int, radius: int, rgb: Tuple[int, int, int], max_alpha: int) -> Any:
    """Composites a smooth radial gradient glow without hard circle edges."""
    from PIL import Image, ImageDraw
    glow = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    steps = 12
    for i in range(steps, 0, -1):
        frac = i / float(steps)
        r = int(radius * frac)
        a = int(max_alpha * ((1.0 - frac * 0.85) ** 2) * 0.35)
        if a > 0 and r > 0:
            gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(rgb[0], rgb[1], rgb[2], a))
    return Image.alpha_composite(base_rgba, glow)


def _encode_gif(frames_rgb: List[Any], duration_ms: int) -> bytes:
    from PIL import Image
    ref = frames_rgb[len(frames_rgb) // 2].quantize(
        colors=96,
        method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.NONE,
    )
    pal_frames = [
        f.quantize(palette=ref, dither=Image.Dither.NONE)
        for f in frames_rgb
    ]
    buf = io.BytesIO()
    pal_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=pal_frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    return buf.getvalue()


def build_animated_hero_gif(
    summary: Dict[str, Any],
    team_name: str,
    league_name: str,
    trigger_label: str,
    active_archetype: str = "LIQUIDITY_VULTURE_PRO",
    llm_label: str = "GEMINI 4 HYDROGEN",
) -> Optional[bytes]:
    """
    Renders a crisp 680x176 looping animated hero card (`cid:hero_banner`)
    with pulsing live beacon, diagonal metallic shimmer sweep, and live telemetry.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    W2, H2 = 1360, 352
    W, H = 680, 176
    n_frames = 14

    mode = str(summary.get("mode", "FULL_AUTO")).upper()
    j_num = summary.get("target_jornada_num", "?")
    date_s = str(summary.get("date", ""))
    plan = summary.get("plan") or {}
    lu = plan.get("lineup") or {}
    formation = str(lu.get("formation", "4-4-2"))
    xp = float(lu.get("expected_points", 0.0) or 0.0)
    posture_info = summary.get("strategy_posture") or {}
    posture = str(posture_info.get("posture") or active_archetype)
    rank_str = (
        f"#{posture_info.get('my_rank')}/{posture_info.get('total_managers')}"
        if posture_info.get("my_rank")
        else ""
    )
    posture_col = _POSTURE_COLORS.get(posture, (56, 189, 248))

    f_tag = _load_font(20, bold=True)
    f_sub_brand = _load_font(20, bold=True)
    f_title = _load_font(48, bold=True)
    f_meta = _load_font(23, bold=False)
    f_rib = _load_font(19, bold=True, mono=True)

    bg_dark = (8, 15, 29)
    base = Image.new("RGBA", (W2, H2), (*bg_dark, 255))
    # Smooth radial glows composited gently so text stays razor sharp
    base = _add_soft_glow(base, 1180, 40, 420, (16, 185, 129), max_alpha=65)
    base = _add_soft_glow(base, 1280, 120, 340, (56, 189, 248), max_alpha=45)
    base = _add_soft_glow(base, 120, 110, 260, (245, 185, 66), max_alpha=40)

    bd = ImageDraw.Draw(base, "RGBA")
    # Outer frame & top emerald accent rail
    bd.rounded_rectangle([2, 2, W2 - 3, H2 - 3], radius=28, outline=(36, 62, 99, 255), width=3)
    bd.rounded_rectangle([40, 2, W2 - 41, 8], radius=4, fill=(16, 185, 129, 255))

    # Left Trophy Crest Box
    bd.rounded_rectangle(
        [40, 42, 168, 170],
        radius=26,
        fill=_blend_rgb(bg_dark, (245, 185, 66), 0.08),
        outline=(245, 185, 66, 255),
        width=4,
    )
    _draw_trophy(bd, 104, 106, scale=2.1, gold=(245, 185, 66, 255))

    # Live Mode Pill
    mode_txt = f"    {mode}  •  {trigger_label}"
    mode_w = int(bd.textlength(mode_txt, font=f_tag)) + 34
    bd.rounded_rectangle(
        [196, 34, 196 + mode_w, 74],
        radius=18,
        fill=_blend_rgb(bg_dark, (16, 185, 129), 0.16),
        outline=(16, 185, 129, 255),
        width=2,
    )
    bd.text((234, 43), mode_txt.strip(), font=f_tag, fill=(167, 243, 208, 255))

    # Right IA Badge
    ia_txt = f"IA • {llm_label}"
    ia_w = int(bd.textlength(ia_txt, font=f_tag)) + 36
    ia_x0 = W2 - 42 - ia_w
    bd.rounded_rectangle(
        [ia_x0, 34, W2 - 42, 74],
        radius=18,
        fill=_blend_rgb(bg_dark, (129, 140, 248), 0.18),
        outline=(129, 140, 248, 255),
        width=2,
    )
    bd.text((ia_x0 + 18, 43), ia_txt, font=f_tag, fill=(224, 231, 255, 255))

    # Headline & League Meta
    bd.text((196, 88), "FANTASY CHAMPION AUTOPILOT", font=f_sub_brand, fill=(52, 211, 153, 255))
    safe_team = _fit_text(bd, team_name, f_title, W2 - 245)
    bd.text((196, 118), safe_team, font=f_title, fill=(248, 250, 252, 255))
    meta_txt = _fit_text(
        bd,
        f"{league_name}   •   Jornada Objetivo J{j_num}   •   {date_s}",
        f_meta,
        W2 - 245,
    )
    bd.text((196, 178), meta_txt, font=f_meta, fill=(186, 200, 220, 255))

    # Bottom Telemetry Ribbon
    bd.rounded_rectangle([40, 246, W2 - 40, 314], radius=18, fill=(11, 23, 40, 255), outline=(34, 58, 92, 255), width=2)
    seg1 = _fit_text(bd, f"ESTRATEGIA: {active_archetype}", f_rib, 515)
    seg2_raw = f"POSTURA: {posture}" + (f" ({rank_str})" if rank_str else "")
    seg2 = _fit_text(bd, seg2_raw, f_rib, 445)
    seg3 = _fit_text(bd, f"ONCE {formation}: {xp:.1f} xP", f_rib, 305)

    bd.text((64, 270), seg1, font=f_rib, fill=(245, 185, 66, 255))
    bd.line([590, 258, 590, 302], fill=(34, 58, 92, 255), width=2)
    bd.text((608, 270), seg2, font=f_rib, fill=(*posture_col, 255))
    bd.line([1048, 258, 1048, 302], fill=(34, 58, 92, 255), width=2)
    bd.text((1066, 270), seg3, font=f_rib, fill=(52, 211, 153, 255))

    frames_rgb: List[Image.Image] = []
    for fi in range(n_frames):
        t = fi / float(n_frames)
        pulse = 0.5 - 0.5 * math.cos(t * 2.0 * math.pi)
        ov = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov, "RGBA")

        # Diagonal specular light sheen across card
        sx = int(-260 + t * (W2 + 520))
        od.polygon(
            [(sx, 0), (sx + 130, 0), (sx - 90, H2), (sx - 220, H2)],
            fill=(255, 255, 255, 13),
        )
        # Animated golden highlight bar gliding along top rail & telemetry footer
        bar_x = int(42 + t * (W2 - 220))
        od.rounded_rectangle([bar_x, 2, bar_x + 140, 8], radius=4, fill=(245, 185, 66, 240))
        od.rounded_rectangle([bar_x, 310, bar_x + 130, 314], radius=2, fill=(56, 189, 248, 210))

        # Pulsing emerald radar beacon inside status pill
        bx, by = 216, 54
        halo_r = int(7 + pulse * 10)
        halo_a = int(175 * (1.0 - pulse))
        if halo_a > 6:
            od.ellipse([bx - halo_r, by - halo_r, bx + halo_r, by + halo_r], fill=(52, 211, 153, halo_a))
        od.ellipse([bx - 5, by - 5, bx + 5, by + 5], fill=(16, 185, 129, 255))

        # Breathing golden halo around crest
        od.rounded_rectangle([35, 37, 173, 175], radius=29, outline=(245, 185, 66, int(55 + 125 * pulse)), width=3)

        composed = Image.alpha_composite(base, ov).convert("RGB")
        frames_rgb.append(composed.resize((W, H), Image.Resampling.LANCZOS))

    return _encode_gif(frames_rgb, duration_ms=85)


def build_animated_pitch_gif(
    lineup: Dict[str, Any],
    jornada_num: Any = "?",
) -> Optional[bytes]:
    """
    Renders a crisp 680x276 animated green stadium pitch (`cid:tactical_pitch`)
    positioning the 11 MILP starters across GK -> DF -> MF -> FW lanes,
    with animated tactical passing pulses and a golden MVP halo ring.
    """
    slots: List[Dict[str, Any]] = list(lineup.get("slots") or [])
    if not slots:
        return None
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    W2, H2 = 1360, 552
    W, H = 680, 276
    n_frames = 12

    formation = str(lineup.get("formation", "4-4-2"))
    total_xp = float(lineup.get("expected_points", 0.0) or 0.0)

    by_line: Dict[str, List[Dict[str, Any]]] = {"GK": [], "DF": [], "MF": [], "FW": []}
    for s in slots:
        ln = str(s.get("linea", "MF")).upper()
        if ln not in by_line:
            ln = "MF"
        by_line[ln].append(s)

    mvp_slot = max(slots, key=lambda s: (float(s.get("xp", 0.0) or 0.0), str(s.get("nombre", ""))))
    mvp_pid = str(mvp_slot.get("player_id") or mvp_slot.get("nombre", ""))

    PX0, PY0, PX1, PY1 = 28, 90, W2 - 28, H2 - 24
    PW, PH = PX1 - PX0, PY1 - PY0

    f_hdr = _load_font(22, bold=True)
    f_xp_hdr = _load_font(20, bold=True, mono=True)
    f_pos = _load_font(16, bold=True)
    f_name = _load_font(20, bold=True)
    f_xp = _load_font(18, bold=True, mono=True)
    f_mvp = _load_font(14, bold=True)

    bg_frame = (10, 21, 38)
    base = Image.new("RGBA", (W2, H2), (*bg_frame, 255))
    bd = ImageDraw.Draw(base, "RGBA")

    # Outer frame + Stadium Header Bar
    bd.rounded_rectangle([2, 2, W2 - 3, H2 - 3], radius=28, outline=(32, 62, 92, 255), width=3)
    bd.text((34, 30), f"ONCE TITULAR ÓPTIMO MILP  •  {formation}  •  J{jornada_num}", font=f_hdr, fill=(230, 237, 247, 255))

    xp_badge = f"★ {total_xp:.1f} xP PROYECTADOS"
    xb_w = int(bd.textlength(xp_badge, font=f_xp_hdr)) + 34
    bd.rounded_rectangle(
        [W2 - 34 - xb_w, 22, W2 - 34, 66],
        radius=18,
        fill=_blend_rgb(bg_frame, (245, 185, 66), 0.14),
        outline=(245, 185, 66, 255),
        width=2,
    )
    bd.text((W2 - 34 - xb_w + 17, 31), xp_badge, font=f_xp_hdr, fill=(245, 185, 66, 255))

    # Mown Grass Turf Stripes (opaque onto rounded turf mask)
    turf = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    td = ImageDraw.Draw(turf, "RGBA")
    n_stripes = 10
    sw = PW / float(n_stripes)
    for si in range(n_stripes):
        sx0 = int(PX0 + si * sw)
        sx1 = int(PX0 + (si + 1) * sw) + 1
        col = (13, 62, 42, 255) if si % 2 == 0 else (16, 74, 50, 255)
        td.rectangle([sx0, PY0, sx1, PY1], fill=col)

    mask = Image.new("L", (W2, H2), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([PX0, PY0, PX1, PY1], radius=22, fill=255)
    base.paste(turf, (0, 0), mask)

    # Chalk Pitch Lines drawn on separate transparent layer & composited cleanly
    chalk_layer = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    cd = ImageDraw.Draw(chalk_layer, "RGBA")
    chalk = (167, 243, 208, 72)
    cd.rounded_rectangle([PX0, PY0, PX1, PY1], radius=22, outline=chalk, width=3)
    mid_x = (PX0 + PX1) // 2
    mid_y = (PY0 + PY1) // 2
    cd.line([mid_x, PY0, mid_x, PY1], fill=chalk, width=3)
    cd.ellipse([mid_x - 68, mid_y - 68, mid_x + 68, mid_y + 68], outline=chalk, width=3)
    cd.ellipse([mid_x - 5, mid_y - 5, mid_x + 5, mid_y + 5], fill=chalk)
    cd.rectangle([PX0, mid_y - 115, PX0 + 130, mid_y + 115], outline=chalk, width=3)
    cd.rectangle([PX1 - 130, mid_y - 115, PX1, mid_y + 115], outline=chalk, width=3)
    cd.rectangle([PX0, mid_y - 56, PX0 + 48, mid_y + 56], outline=chalk, width=2)
    cd.rectangle([PX1 - 48, mid_y - 56, PX1, mid_y + 56], outline=chalk, width=2)

    # Player lane centers
    lane_x = {"GK": 168, "DF": 468, "MF": 820, "FW": 1166}
    card_w, card_h = 276, 68
    nodes: List[Dict[str, Any]] = []
    lane_centers: Dict[str, List[Tuple[int, int]]] = {"GK": [], "DF": [], "MF": [], "FW": []}

    top_pad, bot_pad = PY0 + 18, PY1 - 18
    avail_h = bot_pad - top_pad
    for ln in ("GK", "DF", "MF", "FW"):
        players = by_line[ln]
        k = len(players)
        for idx, p in enumerate(players):
            cy = int(top_pad + avail_h * (idx + 0.5) / float(max(1, k)))
            cx = lane_x[ln]
            pid_key = str(p.get("player_id") or p.get("nombre", ""))
            nodes.append({
                "linea": ln,
                "nombre": str(p.get("nombre", "?")),
                "xp": float(p.get("xp", 0.0) or 0.0),
                "cx": cx,
                "cy": cy,
                "is_mvp": (pid_key == mvp_pid),
            })
            lane_centers[ln].append((cx, cy))

    pass_links: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
    ordered_lanes = [ln for ln in ("GK", "DF", "MF", "FW") if lane_centers[ln]]
    for i in range(len(ordered_lanes) - 1):
        src_pts = lane_centers[ordered_lanes[i]]
        dst_pts = lane_centers[ordered_lanes[i + 1]]
        for sx, sy in src_pts:
            nearest = sorted(dst_pts, key=lambda pt: abs(pt[1] - sy))[:2]
            for nx, ny in nearest:
                pass_links.append(((sx, sy), (nx, ny)))

    for (x1, y1), (x2, y2) in pass_links:
        cd.line([x1, y1, x2, y2], fill=(56, 189, 248, 52), width=3)

    base = Image.alpha_composite(base, chalk_layer)

    card_bg = (9, 20, 35)
    frames_rgb: List[Image.Image] = []
    for fi in range(n_frames):
        t = fi / float(n_frames)
        pulse = 0.5 - 0.5 * math.cos(t * 2.0 * math.pi)
        ov = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov, "RGBA")

        # 1. Animated pass pulses drawn FIRST (so they stay cleanly behind solid player cards)
        for li, ((x1, y1), (x2, y2)) in enumerate(pass_links):
            phase_t = (t + (li * 0.17)) % 1.0
            px = int(x1 + (x2 - x1) * phase_t)
            py = int(y1 + (y2 - y1) * phase_t)
            od.ellipse([px - 7, py - 7, px + 7, py + 7], fill=(56, 189, 248, 125))
            od.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(224, 242, 254, 245))

        # 2. Solid opaque Player Cards (zero line bleed-through!)
        for nd in nodes:
            cx, cy = nd["cx"], nd["cy"]
            x0, y0 = cx - card_w // 2, cy - card_h // 2
            x1, y1 = x0 + card_w, y0 + card_h
            col = _LINE_COLORS.get(nd["linea"], (16, 185, 129))

            if nd["is_mvp"]:
                pad = int(4 + pulse * 6)
                ha = int(95 + 145 * pulse)
                od.rounded_rectangle(
                    [x0 - pad, y0 - pad, x1 + pad, y1 + pad],
                    radius=18,
                    outline=(245, 185, 66, ha),
                    width=4,
                )

            border_col = (245, 185, 66, 255) if nd["is_mvp"] else (*col, 255)
            od.rounded_rectangle([x0, y0, x1, y1], radius=14, fill=(*card_bg, 255), outline=border_col, width=3)

            # Position circle on left (solid blended fill)
            od.ellipse(
                [x0 + 10, cy - 18, x0 + 46, cy + 18],
                fill=_blend_rgb(card_bg, col, 0.18),
                outline=border_col,
                width=2,
            )
            pos_w = od.textlength(nd["linea"], font=f_pos)
            od.text((x0 + 28 - pos_w / 2, cy - 10), nd["linea"], font=f_pos, fill=(*col, 255))

            # Right xP Pill
            xp_txt = f"{nd['xp']:.1f}"
            xp_w = int(od.textlength(xp_txt, font=f_xp)) + 16
            xp_x0 = x1 - 10 - xp_w
            xp_fill = _blend_rgb(card_bg, (245, 185, 66), 0.20) if nd["is_mvp"] else _blend_rgb(card_bg, (16, 185, 129), 0.18)
            xp_fg = (253, 224, 71, 255) if nd["is_mvp"] else (110, 231, 183, 255)
            od.rounded_rectangle([xp_x0, cy - 15, x1 - 10, cy + 15], radius=10, fill=xp_fill)
            od.text((xp_x0 + 8, cy - 11), xp_txt, font=f_xp, fill=xp_fg)

            # Clean Player Name
            max_nw = (xp_x0 - 8) - (x0 + 54)
            clean_nm = _compact_player_name(od, nd["nombre"], f_name, max_nw)
            od.text((x0 + 54, cy - 12), clean_nm, font=f_name, fill=(248, 250, 252, 255))

            if nd["is_mvp"]:
                od.rounded_rectangle([x1 - 54, y0 - 11, x1 - 8, y0 + 9], radius=8, fill=(245, 185, 66, 255))
                od.text((x1 - 47, y0 - 9), "MVP", font=f_mvp, fill=(8, 15, 29, 255))

        composed = Image.alpha_composite(base, ov).convert("RGB")
        frames_rgb.append(composed.resize((W, H), Image.Resampling.LANCZOS))

    return _encode_gif(frames_rgb, duration_ms=105)
