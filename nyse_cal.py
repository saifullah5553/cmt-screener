"""Zero-dependency NYSE/NASDAQ trading-day check (fallback if pandas_market_calendars is unavailable).
Implements the NYSE holiday rules incl. weekend-observance shifts and Good Friday."""
from datetime import date, timedelta

def _nth_weekday(year, month, weekday, n):  # weekday: Mon=0
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)

def _last_weekday(year, month, weekday):
    d = date(year, month, 28) + timedelta(days=4)          # first of next month-ish
    d = date(d.year, d.month, 1) - timedelta(days=1)        # last day of month
    return d - timedelta(days=(d.weekday() - weekday) % 7)

def _easter(year):  # anonymous Gregorian algorithm
    a=year%19; b=year//100; c=year%100; d=b//4; e=b%4; f=(b+8)//25
    g=(b-f+1)//3; h=(19*a+b-d-g+15)%30; i=c//4; k=c%4
    l=(32+2*e+2*i-h-k)%7; m=(a+11*h+22*l)//451
    month=(h+l-7*m+114)//31; day=((h+l-7*m+114)%31)+1
    return date(year, month, day)

def _observed(d):  # Sat -> Fri, Sun -> Mon
    if d.weekday()==5: return d - timedelta(days=1)
    if d.weekday()==6: return d + timedelta(days=1)
    return d

def nyse_holidays(year):
    hols = set()
    hols.add(_observed(date(year,1,1)))                     # New Year's Day
    hols.add(_nth_weekday(year,1,0,3))                      # MLK (3rd Mon Jan)
    hols.add(_nth_weekday(year,2,0,3))                      # Washington's Bday (3rd Mon Feb)
    hols.add(_easter(year) - timedelta(days=2))            # Good Friday
    hols.add(_last_weekday(year,5,0))                      # Memorial Day (last Mon May)
    if year>=2022: hols.add(_observed(date(year,6,19)))    # Juneteenth
    hols.add(_observed(date(year,7,4)))                    # Independence Day
    hols.add(_nth_weekday(year,9,0,1))                     # Labor Day (1st Mon Sep)
    hols.add(_nth_weekday(year,11,3,4))                    # Thanksgiving (4th Thu Nov)
    hols.add(_observed(date(year,12,25)))                  # Christmas
    return hols

def is_trading_day(d):
    if d.weekday()>=5: return False                        # weekend
    return d not in nyse_holidays(d.year)

if __name__=="__main__":
    checks = {
      "2026-07-03 (Fri, Jul4 observed)": date(2026,7,3),
      "2026-07-04 (Sat Independence)":   date(2026,7,4),
      "2026-07-23 (normal Thu)":         date(2026,7,23),
      "2026-01-01 (New Year)":           date(2026,1,1),
      "2026-01-19 (MLK)":                date(2026,1,19),
      "2026-04-03 (Good Friday)":        date(2026,4,3),
      "2026-05-25 (Memorial)":           date(2026,5,25),
      "2026-06-19 (Juneteenth)":         date(2026,6,19),
      "2026-09-07 (Labor Day)":          date(2026,9,7),
      "2026-11-26 (Thanksgiving)":       date(2026,11,26),
      "2026-12-25 (Christmas)":          date(2026,12,25),
      "2026-11-28 (Sat)":                date(2026,11,28),
      "2025-12-25 (Christmas 25)":       date(2025,12,25),
    }
    for label,d in checks.items():
        print(f"  trading={str(is_trading_day(d)):5}  {label}")
