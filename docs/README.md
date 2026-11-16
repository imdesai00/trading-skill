# Documentation

Start here if you are new. Read the files in this order the first time; later, use them as a reference.

| file | read it when |
| --- | --- |
| [HOW_TO_USE.md](HOW_TO_USE.md) | **start here**: the whole thing on one page, with what to say to Claude |
| [GETTING_STARTED.md](GETTING_STARTED.md) | you have just downloaded the pack and want it running in an afternoon |
| [DAILY_ROUTINE.md](DAILY_ROUTINE.md) | you want to know exactly what to do each trading day, and each Friday |
| [SKILLS.md](SKILLS.md) | you want to know what each of the 15 skills does and its commands |
| [SAFETY.md](SAFETY.md) | you want to know what stops a bad trade (and what does not) |
| [CONFIG.md](CONFIG.md) | you want to change a setting, or understand one |
| [DATA_FILES.md](DATA_FILES.md) | you need the daily ASM/GSM, MIS, price-band and VIX files |
| [GO_LIVE.md](GO_LIVE.md) | four weeks of paper trading are done and you are thinking about real money |
| [ARCHITECTURE.md](ARCHITECTURE.md) | you want the code map, the database tables and the data flow |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | something printed an error or looks wrong |
| [GLOSSARY.md](GLOSSARY.md) | a word is new to you (MIS, SL-M, ASM, R multiple ...) |
| [ROADMAP.md](ROADMAP.md) | you want to know the three phases and the exit test for each |
| [PHASE_2_UI.md](PHASE_2_UI.md) | planning the local dashboard (after real-data paper trading works) |
| [DHAN.md](DHAN.md) | the broker is Dhan: what it offers, what is verified, how the pack uses it |
| [FYERS.md](FYERS.md) | FYERS as an alternative broker, and why TradingView paper trading is not integrated |
| [PHASE_3_DHAN.md](PHASE_3_DHAN.md) | planning the Dhan connection (last, and only after the paper period) |
| [AUDIT.md](AUDIT.md) | you want to see this build checked against the specification, and what is still missing |

Two facts to keep in mind everywhere:

1. **Nothing here is financial advice**, and most people who day trade lose money.
2. **Paper trading is the default and nothing places an order without your typed "yes".** Real money needs you to edit your own config
   file, connect Dhan yourself and confirm several settings. No tool in this pack can do that for you.

Also in the repository: [VERIFY.md](../VERIFY.md) lists every outside fact the code depends on, with its source, its date and whether it was
actually checked. Read it before you trust a feed or a charge rate.
