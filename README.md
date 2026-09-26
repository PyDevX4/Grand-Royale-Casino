# Grand Royale Casino

A just-for-fun casino game made with pygame. **Play money only** - chips are an in-game currency with no
cash value, and they can never be bought, sold, or exchanged for real money.

30+ games (blackjack, poker, roulette, slots, craps, baccarat, pinball, Chicken Crossing, cups, a stock
market, lottery and more), trophies, a job system for when you run out of chips, and multiplayer with
friends on the same Wi-Fi (shared poker and blackjack tables, chat).

## Play

Download the `.zip` from the [latest release](https://github.com/PyDevX4/Grand-Royale-Casino/releases/latest),
unzip it and run **Grand Royale Casino.exe**. No Python needed. Windows may warn that it doesn't recognise
the file (it isn't signed) - click **More info**, then **Run anyway**.

The game updates itself: when a new version is out, it downloads it in the background and installs it the
next time you're in the lobby.

## Run from source

```
pip install pygame-ce
python casino.py
```

## Publish an update

```
python build_exe.py --bump --publish
```
