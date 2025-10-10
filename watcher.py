from wildcard import find_window
import time
from sp_mailer import *
from update_sp import *
import psutil
import sys
import os

PID = int(sys.argv[1])

start_time = time.time()

print(PID)

print(os.getpid())
while psutil.pid_exists(PID):
# while find_window("MRO Uploader"):
    # print(psutil.pid_exists(PID))
    # print("still running")
    time.sleep(1)

# print("mail sent")

if not os.path.exists("sp_report.txt"):
    print("Bot crashed without performing any actions. no sharepoint update required.")
    os.system("pause")
    sys.exit()

try:
    _bot_name, _function, _count, _paths, _comments = read_report_file("sp_report.txt")
except:
    sys.exit()
    
user = getpass.getuser()

if user.upper() == "S952716":
    user = "v.alcm"

end_time = time.time()

run_duration_hours = (end_time-start_time)/3600

send_email(_bot_name,_function,int(_count),user, _paths, _comments, run_duration_hours)

# os.system("pause")