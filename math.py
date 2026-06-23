"""
2D Lap-Based Race Game
Controls: W = accelerate, S = brake/reverse, A = steer left, D = steer right
Stay on the track! Going off-road slows you down a lot.
Complete laps by crossing the start/finish line (top of track) going the right way.
"""

import turtle
import math
import time

# ----------------------------- SETUP SCREEN -----------------------------
screen = turtle.Screen()
screen.title("WASD Race Game")
screen.setup(width=900, height=700)
screen.bgcolor("#3a7d44")  # grass green
screen.tracer(0)  # manual screen updates for smooth animation

# ----------------------------- TRACK -----------------------------
# Oval track defined by an outer and inner ellipse (boundaries).
# A point is "on track" if it's between the inner and outer ellipse.
TRACK_CENTER = (0, 0)
OUTER_A, OUTER_B = 380, 260  # outer ellipse radii (x, y)
INNER_A, INNER_B = 230, 130  # inner ellipse radii (x, y)

track_pen = turtle.Turtle()
track_pen.hideturtle()
track_pen.speed(0)
track_pen.penup()


def draw_ellipse(pen, a, b, color, width=4):
    """Draw an ellipse centered at origin with semi-axes a (x) and b (y)."""
    pen.pencolor(color)
    pen.pensize(width)
    pen.penup()
    steps = 100
    first = True
    for i in range(steps + 1):
        angle = 2 * math.pi * i / steps
        x = a * math.cos(angle)
        y = b * math.sin(angle)
        if first:
            pen.goto(x, y)
            pen.pendown()
            first = False
        else:
            pen.goto(x, y)
    pen.penup()


def draw_filled_track():
    # Draw road surface (gray) by filling between outer and inner ellipse
    # using turtle's fill: draw outer filled gray, then inner filled with bg color (grass)
    track_pen.fillcolor("#555555")
    track_pen.begin_fill()
    steps = 100
    track_pen.goto(OUTER_A, 0)
    track_pen.pendown()
    for i in range(steps + 1):
        angle = 2 * math.pi * i / steps
        x = OUTER_A * math.cos(angle)
        y = OUTER_B * math.sin(angle)
        track_pen.goto(x, y)
    track_pen.penup()
    track_pen.end_fill()

    # Cut out inner grass island
    track_pen.fillcolor("#3a7d44")
    track_pen.begin_fill()
    track_pen.goto(INNER_A, 0)
    track_pen.pendown()
    for i in range(steps + 1):
        angle = 2 * math.pi * i / steps
        x = INNER_A * math.cos(angle)
        y = INNER_B * math.sin(angle)
        track_pen.goto(x, y)
    track_pen.penup()
    track_pen.end_fill()

    # Draw boundary lines on top
    draw_ellipse(track_pen, OUTER_A, OUTER_B, "white", 4)
    draw_ellipse(track_pen, INNER_A, INNER_B, "white", 4)


draw_filled_track()

# Start/finish line: a short white line crossing the track at the top (angle = 90deg, i.e. straight up)
finish_pen = turtle.Turtle()
finish_pen.hideturtle()
finish_pen.penup()
finish_pen.color("yellow")
finish_pen.pensize(6)
mid_a = (OUTER_A + INNER_A) / 2
finish_outer_y = OUTER_B
finish_inner_y = INNER_B
finish_pen.goto(0, INNER_B)
finish_pen.pendown()
finish_pen.goto(0, OUTER_B)
finish_pen.penup()


def is_on_track(x, y):
    """Return True if point (x,y) is between inner and outer ellipse (on the road)."""
    outer_val = (x**2) / (OUTER_A**2) + (y**2) / (OUTER_B**2)
    inner_val = (x**2) / (INNER_A**2) + (y**2) / (INNER_B**2)
    return inner_val >= 1 and outer_val <= 1


# ----------------------------- CAR -----------------------------
car = turtle.Turtle()
car.shape("triangle")
car.color("#e63946")
car.shapesize(stretch_wid=1.0, stretch_len=1.6)
car.penup()
car.setheading(
    180
)  # facing left at start (so motion goes counter-clockwise around oval)
start_x, start_y = 0, (OUTER_B + INNER_B) / 2
car.goto(start_x, start_y)

# Physics state
velocity = 0.0
MAX_SPEED = 7.0
MAX_REVERSE = -3.0
ACCELERATION = 0.25
BRAKE_DEACCEL = 0.4
FRICTION = 0.06
TURN_SPEED = 5.0  # degrees per frame, scaled by speed
OFFROAD_MAX_SPEED = 2.0

keys_pressed = set()


def key_down(key):
    keys_pressed.add(key)


def key_up(key):
    keys_pressed.discard(key)


for k in ["w", "a", "s", "d"]:
    screen.onkeypress(lambda k=k: key_down(k), k)
    screen.onkeyrelease(lambda k=k: key_up(k), k)

screen.listen()

# ----------------------------- HUD -----------------------------
hud = turtle.Turtle()
hud.hideturtle()
hud.penup()
hud.color("white")
hud.goto(-440, 300)

TOTAL_LAPS = 3
lap_count = 0
last_y_side = (
    None  # track which side of finish line car was on (for crossing detection)
)
race_started_time = None
race_finished = False
finish_time = None

# To detect a valid lap crossing, require the car to be near the top of track (within road band)
# and moving in the correct direction (heading roughly downward in our oriented angle sense is complex
# with an oval; simplify: track an angle "progress" around the oval instead of using x-y crossing).


# Use angular position around track center for robust lap detection.
def get_angle(x, y):
    return math.atan2(
        y / max(OUTER_B, 1), x / max(OUTER_A, 1)
    )  # normalized angle around ellipse


prev_angle = get_angle(start_x, start_y)
unwrapped_progress = 0.0  # cumulative signed angle traveled around the oval (radians)


def update_hud():
    hud.clear()
    hud.goto(-440, 300)
    status = "OFF ROAD! " if not on_road else ""
    hud.write(
        f"{status}Lap: {min(lap_count, TOTAL_LAPS)}/{TOTAL_LAPS}    Speed: {velocity:.1f}",
        font=("Arial", 16, "normal"),
    )
    hud.goto(-440, 270)
    if race_started_time and not race_finished:
        elapsed = time.time() - race_started_time
        hud.write(f"Time: {elapsed:.1f}s", font=("Arial", 14, "normal"))
    elif race_finished:
        hud.write(f"FINISHED! Time: {finish_time:.1f}s", font=("Arial", 18, "bold"))


on_road = True

# ----------------------------- GAME LOOP -----------------------------


def game_loop():
    global velocity, lap_count, unwrapped_progress, prev_angle
    global race_started_time, race_finished, finish_time, on_road

    if race_finished:
        screen.update()
        screen.ontimer(game_loop, 30)
        return

    # Start timer on first input
    if race_started_time is None and keys_pressed:
        race_started_time = time.time()

    # --- Handle acceleration / braking ---
    if "w" in keys_pressed:
        velocity += ACCELERATION
    elif "s" in keys_pressed:
        velocity -= BRAKE_DEACCEL
    else:
        # natural friction decay toward 0
        if velocity > 0:
            velocity = max(0.0, velocity - FRICTION)
        elif velocity < 0:
            velocity = min(0.0, velocity + FRICTION)

    # Cap speed depending on whether on or off road
    cap = MAX_SPEED if on_road else OFFROAD_MAX_SPEED
    velocity = max(min(velocity, cap), MAX_REVERSE)

    # --- Handle steering (only effective while moving) ---
    if abs(velocity) > 0.05:
        turn_factor = max(0.4, min(1.0, abs(velocity) / MAX_SPEED))
        if "a" in keys_pressed:
            car.left(TURN_SPEED * turn_factor)
        if "d" in keys_pressed:
            car.right(TURN_SPEED * turn_factor)

    # --- Move car ---
    car.forward(velocity)

    x, y = car.position()

    # --- Check on-road status ---
    on_road = is_on_track(x, y)

    # --- Lap detection using angular progress around the oval ---
    angle = get_angle(x, y)
    delta = angle - prev_angle
    # handle wraparound at +-pi
    if delta > math.pi:
        delta -= 2 * math.pi
    elif delta < -math.pi:
        delta += 2 * math.pi
    unwrapped_progress += delta
    prev_angle = angle

    # Car starts heading left (180 deg) at the top of the track; moving forward from there
    # carries it counter-clockwise around the oval, which makes this angle measure increase
    # over a full lap (~ +2*pi).
    if unwrapped_progress >= 2 * math.pi:
        unwrapped_progress -= 2 * math.pi
        lap_count += 1
        if lap_count >= TOTAL_LAPS and not race_finished:
            race_finished = True
            finish_time = time.time() - race_started_time

    update_hud()
    screen.update()
    screen.ontimer(game_loop, 30)


# Instructions text
instructions = turtle.Turtle()
instructions.hideturtle()
instructions.penup()
instructions.color("white")
instructions.goto(-440, -320)
instructions.write(
    "W = Gas | S = Brake/Reverse | A = Left | D = Right   (Stay on the gray road!)",
    font=("Arial", 13, "normal"),
)

update_hud()
game_loop()
screen.mainloop()
