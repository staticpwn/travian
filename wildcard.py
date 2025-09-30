"""
// Title: Elogbot
// Description : downloads BO reports, prepares them for upload, and uploads resultant files to stream
// Author : Mohammad Wissam Farhoud - ALCM
// Date: 03-03-2023
// Version: V1.0
"""


import win32gui


window_titles = []

def windows_cback(hwnd, lParam): # callback function for the EnumWindows function
    window_titles.append((hwnd, win32gui.GetWindowText(hwnd)))

def refresh_window_titles(): # handler for EnumWindows
    del window_titles[:]
    win32gui.EnumWindows(windows_cback, 0)
    return window_titles

def find_window(part_of_title): # find the window handle number that has a title which contains the text "part_of_title"
    window_titles = refresh_window_titles()
    for title in window_titles:
        if str(part_of_title).upper() in str(title[1]).upper():
            return title[0]