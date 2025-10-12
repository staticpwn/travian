

from importlib import reload

import utility_functions
utility_functions = reload(utility_functions)
from utility_functions import *




chrome_path = find_chrome_executable()

# kill_all_chrome()

time.sleep(5)

try:
    with open("diag_pid.txt", "r") as f:
        previous_pid = int(f.read().strip())

        try:
            kill_cdp_chrome(previous_pid)
        except:
            pass
        f.close()
except:
    pass

diag = ensure_cdp_chrome_running(
        chrome_path=chrome_path,           # or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        remote_port=9222,
        user_data_dir=os.path.join(os.path.expanduser("~"), ".cdp_chrome_profile"),
        extra_args=None,            # e.g., ["--disable-extensions"]
        timeout_secs=25.0,
        kill_if_unreachable=True,
    )

with open("diag_pid.txt", "w") as f:
    f.write(str(diag['pid']))
    f.close()

while True:

    period_user = get_user_for_period(period_allocations)

    if period_user is None:
        print("no user allocated for this time period. going to sleep.")
        time.sleep(random.randint(5,30) * random.randint(30,60))
        continue
    else:

        if get_current_account() != period_user:
            
            login_to_account(diag, period_user)

    imported_targets = pd.read_csv('oases.csv')
    imported_targets.sort_values('last_raided', inplace=True)

    filtered_imported_targets = imported_targets[imported_targets["distance"] <= RAIDING_RADIUS_FROM_TERANA]


    ### initial troop count
    
    try:
        ensure_navigate(diag, "send_troops")
        overview_page_html = get_outer_html_from_diag(diag)
        troops = analyze_overview_page(overview_page_html)

        if len(troops) == 0:
            time.sleep(random.randint(30,60) * random.randint(1,3))
            continue


        for name, oasis in filtered_imported_targets.iterrows():

            x,y = eval(oasis['coordinates'])
            
            if distance_between_points(village_coords['terana'], (x, y)) > RAIDING_RADIUS_FROM_TERANA:
                continue

            if (datetime.now().time().hour in range(0,4)) or (datetime.now().time().hour in range(12,16)):
                if distance_between_points(village_coords['terana'], (x, y)) > 14:
                    continue


            if (time.time() < oasis['next_check']):
                # print(f"skipping {oasis['coordinates']}")
                continue
            
            
            tile_html = get_tile_info_html_from_diag(diag, x, y)
            oasis_details = parse_tile_details(tile_html)

            if oasis_details['type'] != 'Unoccupied oasis':
                imported_targets.loc[name, 'next_check'] = time.time() + 3600*24*7
                imported_targets.to_csv("oases.csv", index=False)
                continue

            if oasis_details['troops'] is not None:
                imported_targets.loc[name, 'next_check'] = time.time() + 30*60
                imported_targets.to_csv("oases.csv", index=False)
                continue

            # send_troops(x,y, village_id, troop_sub_dict, cookie_dict)
            if send_oasis_raid(diag):
                imported_targets.loc[name, 'last_raided'] = round(time.time(), 0)
                imported_targets.to_csv("oases.csv", index=False)
                time.sleep(random.uniform(0.0,1.0))
            else:
                print("out of troops. waiting.")
                break
    except:

        # kill_all_chrome()

        kill_cdp_chrome(diag['pid'])

        time.sleep(5)
        
        diag = ensure_cdp_chrome_running(
        chrome_path=chrome_path,           # or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        remote_port=9222,
        user_data_dir=os.path.join(os.path.expanduser("~"), ".cdp_chrome_profile"),
        extra_args=None,            # e.g., ["--disable-extensions"]
        timeout_secs=25.0,
        kill_if_unreachable=True,
    )
        
    # time.sleep(random.randint(30,60) * random.randint(1,3))