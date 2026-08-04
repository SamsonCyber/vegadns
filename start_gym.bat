@echo off
cd /d C:\code\vegadns
if not exist target\release\vegadns.exe cargo build --release
if not exist fixtures\gym\zone_gym.json py -3 scripts\gen_gym_fixtures.py
echo.
echo  Subdomain Scanner Gym TRUE TEST
echo  http://127.0.0.1:9876/
echo  Default mode: mock-stress (latency+SERVFAIL+drop)
echo  Not "fastest on the market". Leave window open. Ctrl+C stops.
echo.
start http://127.0.0.1:9876/
py -3 scripts\gym_server.py --host 127.0.0.1 --port 9876 --out C:\code\vegadns\gym_out --wordlist-cap 5000
pause
