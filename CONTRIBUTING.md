# Contributing

Thanks for looking. Bug reports, questions and pull requests are all welcome.

## Before you open a pull request

```bash
# tests (standard library only, no network)
cd telegram-bot && python3 -m unittest discover -s tests -t .

# secret scan - run it from the repository root
tools/check-secrets.sh
```

Both must pass. If you add or change anything the user sees, add the text to **both** language
blocks in `telegram-bot/messages.py`; the test suite fails when the key sets differ.

## House rules

- **No dependencies.** The bot is standard library only, so it runs on a Raspberry Pi with
  nothing installed. Please keep it that way.
- **The bot reads, it does not write.** Anything that changes the heating belongs in a Home
  Assistant automation, where the user can see and disable it.
- **Never commit real data.** No tokens, no `.env`, no `state.json`, no CSV or log files from a
  real install, no internal IP addresses or hostnames. `.gitignore` and the secret scanner help,
  but they are not a substitute for looking at your own diff. Do not bypass them with
  `git commit -n`.
- **Numbers must be provable.** Please do not add savings claims that are not backed by a meter
  reading.
- Keep code comments explaining *why*, not *what*.

## Adding a language

1. Copy the `"en"` block in `telegram-bot/messages.py` and translate the values, keeping every
   `{placeholder}`.
2. Add entries to `WEEKDAYS`, `PLURALS` and `DECIMAL_SEPARATOR` for your language code.
3. Run the tests: a missing key or a broken placeholder fails immediately.
4. Note your language in the README table of commands if it needs aliases.

## Reporting a bug

Include your Home Assistant version, how you run the bot, and the relevant part of
`journalctl -u hive-bot`. Redact tokens, addresses and chat ids first — `--check` output
contains your entity ids and Home Assistant address.
