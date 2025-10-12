import pyautogui as pa
from datetime import datetime
from datetime import time as dtime

tween_functions = [
    pa.linear,         # constant speed
    pa.easeInQuad,     # slow start, accelerate
    pa.easeOutQuad,    # fast start, decelerate
    pa.easeInOutQuad,  # accelerate then decelerate
    pa.easeInCubic,
    pa.easeOutCubic,
    pa.easeInOutCubic,
    pa.easeInQuart,
    pa.easeOutQuart,
    pa.easeInOutQuart,
    pa.easeInQuint,
    pa.easeOutQuint,
    pa.easeInOutQuint,
    pa.easeInSine,
    pa.easeOutSine,
    pa.easeInOutSine,
    pa.easeInExpo,
    pa.easeOutExpo,
    pa.easeInOutExpo,
    pa.easeInCirc,
    pa.easeOutCirc,
    pa.easeInOutCirc,
    pa.easeInElastic,
    pa.easeOutElastic,
    pa.easeInOutElastic,
    pa.easeInBack,
    pa.easeOutBack,
    pa.easeInOutBack,
    pa.easeInBounce,
    pa.easeOutBounce,
    pa.easeInOutBounce
]

accounts = {
    "NobelSword": {
        "email": "mohammad.wissam.farhoud@hotmail.com",
        "password": "express_19915",
        "current": True,
    },
    "daggerman": {
        "email": "farhoud.solutions@gmail.com",
        "password": "aboodf2000",
        "current": False,
    },
    "el_hammar": {
        "email": "masterking11@gmail.com",
        "password": "Staticpwn",
        "current": False,
    }
}

target_urls = {
    "login": "https://www.travian.com/international#loginLobby",
    "account": "https://lobby.legends.travian.com/account",
    "tile_base": "https://ts2.x1.international.travian.com/karte.php?", # add f"x={x_coord}&y={y_coord}""
    "terana_village": "https://ts2.x1.international.travian.com/dorf2.php?newdid=31591", #must navigate to terana after login before starting to check troops
    "send_troops": "https://ts2.x1.international.travian.com/build.php?id=39&gid=16&tt=2",
}

TTT_BOX_NUMBER = 1
TTT_RAID_COUNT = 3
RAIDING_RADIUS_FROM_TERANA = 30

village_coords = {
    "terana": (70, -2),
}

period_allocations = {
    "NobelSword": (dtime(5, 30), dtime(13, 0)),
    "daggerman": (dtime(14, 0), dtime(17, 0)),
    "el_hammar": (dtime(18, 0), dtime(4, 30)),
}