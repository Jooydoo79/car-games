"""
Cockpit-View 2D/Pseudo-3D Race Game (OutRun-style)
----------------------------------------------------
Controls:
    W       = accelerate
    S       = brake / reverse
    A / D   = steer left / right
    ESC     = quit

You sit in the driver's seat. The road curves toward you, hills rise and fall,
and a dashboard/cockpit overlay frames the view. Stay on the road -- going off
onto the grass/rumble strips slows you down hard. Complete 3 laps to finish.

Requires: pygame   (install with:  pip install pygame )
"""

import math
import sys
import random
import pygame

# ----------------------------------------------------------------------------
# CONFIG / CONSTANTS
# ----------------------------------------------------------------------------
WIDTH, HEIGHT = 900, 600
HORIZON_Y = HEIGHT // 2

SEGMENT_LENGTH = 200  # length of one road segment, in world units
ROAD_WIDTH = 1600  # half-width of the road, in world units
RUMBLE_LENGTH = 3  # how many segments make up one rumble-strip stripe
CAMERA_HEIGHT = 1000
CAMERA_DEPTH = 0.84  # field-of-view factor (lower = wider fov)
DRAW_DISTANCE = 200  # how many segments ahead we render
FOG_DISTANCE = 160  # segments after which fog fully hides the road

FPS = 60

# Car / player physics
MAX_SPEED = SEGMENT_LENGTH * 60  # world units per second at top speed
ACCEL = MAX_SPEED / 2.5
BRAKE_DECEL = -MAX_SPEED / 1.2
OFFROAD_MAX_SPEED = MAX_SPEED / 4
FRICTION_DECEL = -MAX_SPEED / 6
CENTRIFUGAL = 0.176  # how much curves push the car sideways (tuned so the sharpest
# curve on the track needs ~65% of max steering input to hold --
# noticeable, but always recoverable with full counter-steer)
STEER_SPEED = 2.6  # how fast player_x changes per second when steering

TOTAL_LAPS = 3

# Colors
COLOR_SKY_TOP = (60, 140, 210)
COLOR_SKY_BOTTOM = (170, 210, 235)
COLOR_GRASS_LIGHT = (40, 130, 60)
COLOR_GRASS_DARK = (32, 110, 50)
COLOR_RUMBLE_LIGHT = (220, 220, 220)
COLOR_RUMBLE_DARK = (170, 40, 40)
COLOR_ROAD_LIGHT = (90, 90, 95)
COLOR_ROAD_DARK = (80, 80, 85)
COLOR_LANE = (220, 220, 220)
COLOR_WHITE = (255, 255, 255)
COLOR_BLACK = (10, 10, 10)
COLOR_FINISH_A = (250, 250, 250)
COLOR_FINISH_B = (20, 20, 20)


# ----------------------------------------------------------------------------
# TRACK DEFINITION
# ----------------------------------------------------------------------------
class Segment:
    __slots__ = ("index", "curve", "y", "world_x", "world_y", "looks_finish")

    def __init__(self, index, curve=0.0, y=0.0):
        self.index = index
        self.curve = curve  # curvature added at this segment
        self.y = y  # hill height at this segment (world units)
        self.world_x = 0.0  # cumulative lateral offset (computed after creation)
        self.world_y = 0.0  # cumulative height offset (computed after creation)
        self.looks_finish = False


def build_track():
    """
    Build a closed-loop track as a list of Segment objects.

    The "interesting" part of the layout is authored by hand (straights, curves,
    hills). Because an arbitrary mix of curve segments will NOT generally bring the
    road back to its starting heading and lateral position, two closing curve
    segments are appended at the end, with their curvature solved in closed form so
    that the track returns EXACTLY to heading 0 and lateral position 0 -- i.e. the
    loop closes cleanly with no seam/jump when lap N wraps into lap N+1.

    Derivation (verified numerically before use):
      Let H, X be the residual heading/lateral-position after the hand-authored
      segments. Appending a block of N1 segments of constant curve c1, followed by
      a block of N2 segments of constant curve c2, and solving heading==0 and
      position==0 for (c1, c2) gives:
          c1 = (-2*H*N1 - H*N2 + H - 2*X) / (N1**2 + N1*N2)
          c2 = (H*N1 - H + 2*X) / (N1*N2 + N2**2)
    """
    layout = [
        ("straight", 20, 0.0),
        ("curve", 40, 0.9),  # sweeping right
        ("straight", 15, 0.0),
        ("hill", 30, 900.0),  # gentle hill while mostly straight
        ("curve", 35, -1.3),  # sharper left
        ("straight", 15, 0.0),
        ("curve", 50, 1.6),  # sharp right hairpin-ish
        ("hill", 25, -500.0),  # dip back down
        ("straight", 20, 0.0),
        ("curve", 30, -0.7),  # easy left back toward home straight
        ("straight", 25, 0.0),  # final approach
    ]

    curve_seq = []
    height_seq = []
    for kind, n, amount in layout:
        if kind == "hill":
            for i in range(n):
                t = i / max(1, n - 1)
                height_seq.append(amount * math.sin(t * math.pi))
                curve_seq.append(0.0)
        else:
            curve_amount = amount if kind == "curve" else 0.0
            for _ in range(n):
                curve_seq.append(curve_amount)
                height_seq.append(0.0)

    # Measure residual heading (H) and lateral position (X) after the hand-authored part.
    H, X = 0.0, 0.0
    for c in curve_seq:
        H += c
        X += H

    # Two closing curve blocks, solved exactly to zero out both H and X.
    N1, N2 = 130, 130
    c1 = (-2 * H * N1 - H * N2 + H - 2 * X) / (N1**2 + N1 * N2)
    c2 = (H * N1 - H + 2 * X) / (N1 * N2 + N2**2)
    curve_seq.extend([c1] * N1 + [c2] * N2)
    height_seq.extend([0.0] * (N1 + N2))

    # Build Segment objects and integrate cumulative curve (world_x) for real.
    segments = []
    total_curve = 0.0
    x = 0.0
    for i, (c, h) in enumerate(zip(curve_seq, height_seq)):
        seg = Segment(i, curve=c, y=h)
        total_curve += c
        x += total_curve
        seg.world_x = x
        seg.world_y = h
        segments.append(seg)

    segments[0].looks_finish = True
    return segments


TRACK = build_track()
TRACK_LENGTH = len(TRACK)


def segment_at(z_index):
    return TRACK[z_index % TRACK_LENGTH]


# ----------------------------------------------------------------------------
# PROJECTION
# ----------------------------------------------------------------------------
def project(world_x, world_y, world_z, cam_x, cam_y, cam_z, cam_depth=CAMERA_DEPTH):
    dx = world_x - cam_x
    dy = world_y - cam_y
    dz = world_z - cam_z
    if dz < 1:
        dz = 1
    scale = cam_depth / dz
    screen_x = (WIDTH / 2) + scale * dx * (WIDTH / 2)
    screen_y = (HEIGHT / 2) - scale * dy * (HEIGHT / 2)
    screen_w = scale * ROAD_WIDTH * (WIDTH / 2)
    return screen_x, screen_y, screen_w, scale


# ----------------------------------------------------------------------------
# SCENERY (simple trees/poles for a sense of speed, placed at fixed track positions)
# ----------------------------------------------------------------------------
def build_scenery():
    """Return a dict mapping segment index -> list of (side, kind) decorations."""
    decor = {}
    rng = random.Random(42)
    for i in range(0, TRACK_LENGTH, 4):
        if rng.random() < 0.7:
            side = rng.choice([-1, 1])
            kind = rng.choice(["tree", "tree", "pole"])
            decor.setdefault(i, []).append((side, kind))
    return decor


SCENERY = build_scenery()


def draw_tree(surface, x, y, scale):
    h = max(4, int(140 * scale))
    w = max(3, int(90 * scale))
    trunk_h = max(2, int(h * 0.35))
    trunk_w = max(2, int(w * 0.18))
    # trunk
    pygame.draw.rect(
        surface,
        (90, 60, 30),
        (int(x - trunk_w / 2), int(y - trunk_h), trunk_w, trunk_h),
    )
    # foliage (triangle stack)
    pygame.draw.polygon(
        surface,
        (20, 90, 35),
        [(x, y - h), (x - w / 2, y - trunk_h), (x + w / 2, y - trunk_h)],
    )


def draw_pole(surface, x, y, scale):
    h = max(6, int(160 * scale))
    w = max(2, int(8 * scale))
    pygame.draw.rect(surface, (200, 60, 40), (int(x - w / 2), int(y - h), w, h))
    pygame.draw.circle(
        surface, (240, 230, 60), (int(x), int(y - h)), max(2, int(6 * scale))
    )


# ----------------------------------------------------------------------------
# ROAD RENDERING
# ----------------------------------------------------------------------------
def render_road(surface, base_segment_index, player_x, player_z_offset, cam_y_extra):
    """
    Draw the road from far to near (we compute far->near but must paint near->far
    on screen would be wrong -- actually for a 2D scanline road we paint from the
    FARTHEST segment first up to the NEAREST, since nearer segments must be drawn
    on top to occlude farther ones drawn at the same screen rows in degenerate cases,
    and because each strip is a trapezoid covering a screen-row band).
    """
    # Sky
    surface.fill(COLOR_SKY_TOP, (0, 0, WIDTH, HORIZON_Y))
    pygame.draw.rect(surface, COLOR_SKY_BOTTOM, (0, HORIZON_Y - 60, WIDTH, 60))
    surface.fill(COLOR_GRASS_DARK, (0, HORIZON_Y, WIDTH, HEIGHT - HORIZON_Y))

    base_segment = segment_at(base_segment_index)
    base_world_z = base_segment_index * SEGMENT_LENGTH

    cam_x = base_segment.world_x + player_x * ROAD_WIDTH
    cam_y = CAMERA_HEIGHT + base_segment.world_y + cam_y_extra
    cam_z = base_world_z - player_z_offset  # slight offset within the current segment

    max_screen_y = HEIGHT  # nothing drawn below this yet (start at bottom of screen)

    draw_list = []
    for n in range(DRAW_DISTANCE):
        seg_index = base_segment_index + n
        seg = segment_at(seg_index)
        world_z = seg_index * SEGMENT_LENGTH

        sx, sy, sw, scale = project(
            seg.world_x, seg.world_y, world_z, cam_x, cam_y, cam_z
        )

        if sy >= max_screen_y:
            continue  # this segment is hidden behind a nearer one already drawn

        draw_list.append((n, seg, sx, sy, sw, scale, world_z))
        max_screen_y = sy

    # draw_list currently ordered near-to-far (n increasing) but each entry's
    # max_screen_y only decreases, so to correctly paint without gaps we must
    # render FAR -> NEAR (reverse), so closer (bigger) trapezoids overwrite
    # the far ones' bottom edge area and the horizon stays clean.
    for n, seg, sx, sy, sw, scale, world_z in reversed(draw_list):
        fog = min(1.0, n / FOG_DISTANCE) ** 2 if n > FOG_DISTANCE * 0.3 else 0.0

        # Determine next segment's projected screen position to know the strip's bottom edge.
        next_index = base_segment_index + n + 1
        next_seg = segment_at(next_index)
        next_world_z = next_index * SEGMENT_LENGTH
        nsx, nsy, nsw, nscale = project(
            next_seg.world_x, next_seg.world_y, next_world_z, cam_x, cam_y, cam_z
        )

        is_rumble_dark = (seg.index // RUMBLE_LENGTH) % 2 == 0
        road_color = COLOR_ROAD_LIGHT if (seg.index // 3) % 2 == 0 else COLOR_ROAD_DARK
        grass_color = (
            COLOR_GRASS_LIGHT if (seg.index // 3) % 2 == 0 else COLOR_GRASS_DARK
        )
        rumble_color = COLOR_RUMBLE_DARK if is_rumble_dark else COLOR_RUMBLE_LIGHT

        top_y = max(HORIZON_Y, min(sy, HEIGHT))
        bot_y = max(HORIZON_Y, min(nsy, HEIGHT))
        if bot_y <= top_y:
            bot_y = top_y + 1

        # Grass strip (full width band) -- draw a wide rect behind road for this scanline band
        pygame.draw.rect(surface, grass_color, (0, top_y, WIDTH, bot_y - top_y))

        # Road trapezoid (approximate using a polygon between this segment's width and next's)
        road_poly = [
            (sx - sw, sy),
            (sx + sw, sy),
            (nsx + nsw, nsy),
            (nsx - nsw, nsy),
        ]
        pygame.draw.polygon(surface, road_color, road_poly)

        # Rumble strips (slightly wider than road on both edges)
        rumble_w_near = sw * 1.15
        rumble_w_far = nsw * 1.15
        left_rumble = [
            (sx - rumble_w_near, sy),
            (sx - sw, sy),
            (nsx - nsw, nsy),
            (nsx - rumble_w_far, nsy),
        ]
        right_rumble = [
            (sx + sw, sy),
            (sx + rumble_w_near, sy),
            (nsx + rumble_w_far, nsy),
            (nsx + nsw, nsy),
        ]
        pygame.draw.polygon(surface, rumble_color, left_rumble)
        pygame.draw.polygon(surface, rumble_color, right_rumble)

        # Lane markings (two dashed lines)
        if (seg.index // 3) % 2 == 0:
            lane_w_near = sw * 0.04
            lane_w_far = nsw * 0.04
            for frac in (-0.33, 0.33):
                lane_poly = [
                    (sx + frac * sw - lane_w_near, sy),
                    (sx + frac * sw + lane_w_near, sy),
                    (nsx + frac * nsw + lane_w_far, nsy),
                    (nsx + frac * nsw - lane_w_far, nsy),
                ]
                pygame.draw.polygon(surface, COLOR_LANE, lane_poly)

        # Finish line checker stripe
        if seg.looks_finish:
            stripe_h = max(2, sy - nsy)
            squares = 12
            for col in range(squares):
                cx0 = sx - sw + (2 * sw) * (col / squares)
                cx1 = sx - sw + (2 * sw) * ((col + 1) / squares)
                color = COLOR_FINISH_A if col % 2 == 0 else COLOR_FINISH_B
                pygame.draw.polygon(
                    surface, color, [(cx0, sy), (cx1, sy), (cx1, nsy), (cx0, nsy)]
                )

        # Scenery
        if seg.index in SCENERY and scale > 0.0005:
            for side, kind in SCENERY[seg.index]:
                deco_x = sx + side * (sw * 1.6 + 10)
                if kind == "tree":
                    draw_tree(surface, deco_x, sy, max(scale * 900, 0.05))
                else:
                    draw_pole(surface, deco_x, sy, max(scale * 900, 0.05))

        # Fog fade near draw distance limit
        if fog > 0:
            fog_overlay = pygame.Surface(
                (WIDTH, max(1, int(bot_y - top_y))), pygame.SRCALPHA
            )
            fog_overlay.fill((170, 200, 220, int(180 * fog)))
            surface.blit(fog_overlay, (0, top_y))


# ----------------------------------------------------------------------------
# COCKPIT OVERLAY
# ----------------------------------------------------------------------------
def draw_cockpit(surface, speed_ratio, steer_input):
    """Draw a simple dashboard / hood / steering wheel hint in the foreground."""
    dash_h = 130
    dash_rect = (0, HEIGHT - dash_h, WIDTH, dash_h)
    pygame.draw.rect(surface, (25, 22, 20), dash_rect)
    pygame.draw.rect(surface, (15, 13, 12), (0, HEIGHT - dash_h, WIDTH, 10))

    # Hood corners (dark trapezoids suggesting the car's hood/A-pillars at screen edges)
    pygame.draw.polygon(
        surface,
        (18, 16, 15),
        [(0, HEIGHT), (0, HEIGHT - 230), (140, HEIGHT - dash_h), (0, HEIGHT - dash_h)],
    )
    pygame.draw.polygon(
        surface,
        (18, 16, 15),
        [
            (WIDTH, HEIGHT),
            (WIDTH, HEIGHT - 230),
            (WIDTH - 140, HEIGHT - dash_h),
            (WIDTH, HEIGHT - dash_h),
        ],
    )

    # Steering wheel (rotates slightly with steer input)
    wheel_center = (WIDTH // 2, HEIGHT - 20)
    wheel_radius = 70
    wheel_surf = pygame.Surface(
        (wheel_radius * 2 + 20, wheel_radius * 2 + 20), pygame.SRCALPHA
    )
    wc = (wheel_radius + 10, wheel_radius + 10)
    pygame.draw.circle(wheel_surf, (35, 35, 38), wc, wheel_radius, 14)
    pygame.draw.circle(wheel_surf, (60, 60, 65), wc, 12)
    angle = steer_input * 28  # degrees
    rad = math.radians(angle)
    spoke_len = wheel_radius - 8
    for spoke_angle in (90, 210, 330):
        a = rad + math.radians(spoke_angle)
        ex = wc[0] + spoke_len * math.cos(a)
        ey = wc[1] + spoke_len * math.sin(a)
        pygame.draw.line(wheel_surf, (50, 50, 54), wc, (ex, ey), 10)
    rotated = pygame.transform.rotate(
        wheel_surf, 0
    )  # geometry already rotated via spoke angles
    surface.blit(
        rotated,
        (
            wheel_center[0] - rotated.get_width() // 2,
            wheel_center[1] - rotated.get_height() // 2,
        ),
    )

    # Speedometer (simple arc + needle) on the right side of dash
    speedo_center = (WIDTH - 110, HEIGHT - 45)
    pygame.draw.circle(surface, (40, 40, 44), speedo_center, 45)
    pygame.draw.circle(surface, (20, 20, 24), speedo_center, 45, 4)
    needle_angle = math.radians(
        180 - speed_ratio * 180
    )  # sweep from left(0) to right(max)
    nx = speedo_center[0] + 36 * math.cos(needle_angle)
    ny = speedo_center[1] - 36 * math.sin(needle_angle)
    pygame.draw.line(surface, (220, 60, 50), speedo_center, (nx, ny), 4)
    pygame.draw.circle(surface, (200, 200, 200), speedo_center, 5)


# ----------------------------------------------------------------------------
# HUD TEXT
# ----------------------------------------------------------------------------
def draw_hud(
    surface, font, lap, total_laps, elapsed, finished, finish_time, off_road, speed_kmh
):
    lines = []
    lap_text = f"Lap {min(lap, total_laps)}/{total_laps}"
    lines.append((lap_text, COLOR_WHITE))
    lines.append((f"Speed {int(speed_kmh)} km/h", COLOR_WHITE))
    if off_road:
        lines.append(("OFF ROAD!", (255, 80, 80)))
    if not finished:
        lines.append((f"Time {elapsed:0.1f}s", COLOR_WHITE))
    else:
        lines.append((f"FINISHED! {finish_time:0.1f}s", (255, 230, 80)))

    y = 12
    for text, color in lines:
        rendered = font.render(text, True, color)
        shadow = font.render(text, True, (0, 0, 0))
        surface.blit(shadow, (14, y + 2))
        surface.blit(rendered, (12, y))
        y += rendered.get_height() + 4


# ----------------------------------------------------------------------------
# MAIN GAME
# ----------------------------------------------------------------------------
def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Cockpit Racer")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 22, bold=True)
    big_font = pygame.font.SysFont("Arial", 48, bold=True)

    player_x = 0.0  # -1.0 (left edge) .. +1.0 (right edge) of road, 0 = center
    speed = 0.0  # world units / second
    position_z = 0.0  # absolute distance traveled along the track (world units)

    lap = 0
    last_segment_index = 0

    started = False
    start_ticks = 0
    finished = False
    finish_time = 0.0

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        dt = min(dt, 1 / 20)  # clamp in case of hiccups

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        keys = pygame.key.get_pressed()

        if not finished:
            if keys[pygame.K_w]:
                if not started:
                    started = True
                    start_ticks = pygame.time.get_ticks()
                speed += ACCEL * dt
            elif keys[pygame.K_s]:
                speed += BRAKE_DECEL * dt
            else:
                speed += FRICTION_DECEL * dt

            base_segment_index = int(position_z // SEGMENT_LENGTH)
            current_segment = segment_at(base_segment_index)

            on_road = abs(player_x) <= 1.0
            speed_cap = MAX_SPEED if on_road else OFFROAD_MAX_SPEED
            speed = max(0.0, min(speed, speed_cap))

            steer_input = 0.0
            if keys[pygame.K_a]:
                steer_input = -1.0
            elif keys[pygame.K_d]:
                steer_input = 1.0

            speed_ratio_for_steer = max(0.15, speed / MAX_SPEED)
            player_x += steer_input * STEER_SPEED * speed_ratio_for_steer * dt

            # Centrifugal force from track curvature pushes the player outward in curves
            player_x -= (
                current_segment.curve * CENTRIFUGAL * (speed / MAX_SPEED) * dt * 6
            )

            player_x = max(
                -1.6, min(1.6, player_x)
            )  # allow slightly past edge before "off road" feels harsh

            position_z += speed * dt
            if position_z < 0:
                position_z += TRACK_LENGTH * SEGMENT_LENGTH
            position_z %= TRACK_LENGTH * SEGMENT_LENGTH

            new_segment_index = int(position_z // SEGMENT_LENGTH)
            if (
                new_segment_index < last_segment_index
                and last_segment_index > TRACK_LENGTH - 10
            ):
                lap += 1
                if lap >= TOTAL_LAPS:
                    finished = True
                    finish_time = (pygame.time.get_ticks() - start_ticks) / 1000.0
            last_segment_index = new_segment_index

            off_road_flag = abs(player_x) > 1.0
        else:
            off_road_flag = False
            steer_input = 0.0

        base_segment_index = int(position_z // SEGMENT_LENGTH)
        z_offset_within_segment = position_z % SEGMENT_LENGTH

        render_road(
            screen, base_segment_index, player_x, z_offset_within_segment, cam_y_extra=0
        )
        draw_cockpit(screen, speed / MAX_SPEED, steer_input)

        elapsed = (pygame.time.get_ticks() - start_ticks) / 1000.0 if started else 0.0
        speed_kmh = (
            (speed / SEGMENT_LENGTH) * 3.6 * 10
        )  # arbitrary scale for a readable km/h-ish number
        draw_hud(
            screen,
            font,
            lap,
            TOTAL_LAPS,
            elapsed,
            finished,
            finish_time,
            off_road_flag,
            speed_kmh,
        )

        if not started and not finished:
            tip = font.render(
                "W = Gas    S = Brake    A / D = Steer", True, COLOR_WHITE
            )
            screen.blit(tip, (WIDTH // 2 - tip.get_width() // 2, HEIGHT // 2 - 100))

        if finished:
            msg = big_font.render("FINISHED!", True, (255, 230, 80))
            screen.blit(msg, (WIDTH // 2 - msg.get_width() // 2, HEIGHT // 2 - 140))

        pygame.display.flip()

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
