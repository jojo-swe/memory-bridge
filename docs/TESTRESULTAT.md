## 0.7.0 – komplett loopback-labb och mindre klientmetadata

[Run 40188868175](https://github.com/jojo-swe/memory-bridge/actions/runs/40188868175):
**119 godkända tester, inga överhoppade**, commit
`0909ab4c2f5c06483acc6b494c4b7a47d3e7e05e`.
Lokalt: 108 godkända, elva integrationstester överhoppade. Wheelbygge lyckades.

Verifierat i CI:

- Hela labbet med tillfällig TLS-nyckel, givare, jämförelse och städning, startat utanför repot.
- 64 KiB-anrop mot riktig NBD-givare under 128 MiB RLIMIT_AS per klientprocess.
- Bitmap: 32 bytes för 256 block i ett 1 MiB-export, med fungerande fjärrcache.
- Sammanhängande batcher, blandade datakällor, partiella kanter och sena batchfel.
- Randomiserade läsningar/skrivningar jämförda med referens före/efter cacheförlust.
- Ctrl+C/SystemExit spärrar osäkra I/O och städar nätverksprocesser; shutdown försöker alla bakändar.
- De tidigare testerna för fryst/död givare, fel nyckel och exportstorlek.

[Benchmarkrapport](benchmark-ci-0.7.0.json): ett litet loopback-prov med varm
filcache. Fjärrläget var fortsatt långsammare. RLIMIT_AS är en adressrymdsgräns,
inte mätning av systemets RAM-brist. Varken fysisk LAN-prestanda, beständig
kraschåterställning eller system-swap har verifierats.

## 0.6.0 – jämförande benchmark

[Run 40186254875](https://github.com/jojo-swe/memory-bridge/actions/runs/40186254875):
**93 godkända tester, inga överhoppade**, commit
`ba79b83779c1e4ca3c0e67e5581b1dee1b1285f6`.
Lokalt: 84 godkända och nio transporttester överhoppade. Paketbygge lyckades.

Ett enda litet CI-prov, 1 MiB och 16 läsningar/skrivningar per läge, verifierade
att jämförelsen använder en frisk TLS/NBD-cache. Medianläsning var 0,00247 ms
lokalt och 0,18668 ms med fjärrcache. Medianskrivning var 0,00308 respektive
0,20301 ms. Klientens RSS-topp var 21128 respektive 21228 KiB; NBD-barnets
separata topp var 25660 KiB. Givarens minne och systemfilcache ingår inte.

Fjärrläget var långsammare i detta prov. Det är en funktionskontroll med varm
filcache på loopback, inte en tillförlitlig studie av SSD, LAN eller minnestryck.
[Full rårapport](benchmark-ci-0.6.0.json) innehåller seed, mätvärden och begränsningar.
Regressioner fångar tappade skrivningar, korrupta läsningar, dold lokal fallback
och kontrollerar att timeout stoppar hela experimentets processgrupp.

## 0.5.0 – verklig nätverksåterhämtning

[Run 40185556989](https://github.com/jojo-swe/memory-bridge/actions/runs/40185556989):
**80 godkända tester, inga överhoppade**, commit
`1d0b8f0bc1dc4d2ed45a62c10a5b1fcade931bb9`.

Återhämtningslagret klarade både SIGKILL och SIGSTOP av riktig nbdkit-givare
med TLS på loopback. Data kunde läsas från den lokala filkopian inom testets
3-sekundersgräns och därefter skrivas igen. Hälsosam fjärrläsning verifierades
före avbrottet för att lokal fallback inte skulle maskera transportfel.
Fel nyckel och exportstorlek avvisades. Sju transportenhetstester täcker
bland annat fragmenterade svar, timeout, protokollfel och processavslut.

Lokalt: 72 godkända, åtta NBD-tester överhoppade på grund av saknade
systemberoenden. Paketbygge av 0.5.0 lyckades. Fysiskt LAN, minnestryck,
prestandavinst och system-swap är fortfarande inte verifierade.

## 0.4.0 – CI verifierad

[Run 40184413461](https://github.com/jojo-swe/memory-bridge/actions/runs/40184413461):
**69 godkända tester, inga överhoppade**, på commit
`7a67aedf5fd718939aacd29b1cf4913901bd691f`. Båda demokommandona passerade.
NBD/TLS-testerna avser den befintliga transporten; det nya återhämtningslagret
testades med lokal fil och simulerad givare.

## 0.4.0 – lokal verifiering

69 tester: 65 godkända, 4 NBD/TLS-tester hoppas över eftersom systempaketen
saknas i utvecklingsmiljön. Återhämtningsdemon med riktig temporär fil klarar
simulerat givaravbrott och fortsatta skrivningar. Ett deterministiskt test
jämför 200 överlappande skrivningar med en referens, före och efter avbrott.
CI kör samma svit med obligatoriska NBD-beroenden och tillåter inga hoppade tester.
Detta är ingen verifiering av återhämtning över nätverk eller system-swap.

# Verifiering 2026-10-25 — ramverk 0.2.1

Miljö: Ubuntu 24.04.3, Python 3.12.14, memlock-gräns 8192 KiB.

Kommando: `python3 -m unittest discover -s tests -v`

Resultat: **36 godkända tester, 4 överhoppade transporttester (40 totalt).**

Verifierat: isolerade allokeringar, ägarkvot, totalbudget, sessionsgräns,
40 samtidiga allokeringsförsök mot fyra tillgängliga platser, 500 randomiserade
skriv/läsjämförelser, gränskontroll, konfiguration, korta läsningar, injicerade
läs-/skrivfel, fel vid frigöring, karantän och avveckling trots enskilda fel.
Även verkligt låst RAM, fatal låsningsbrist, nyckelfilsrättigheter och
NBD-pluginens callbacks testades. Callbacktest är inte transporttest.

Kommandona `python3 -m memory_bridge demo`, `doctor` och `validate-config
config/local.toml` kördes framgångsrikt. Demon använder simulerade minneslager
och visar isolation, spärrad trasig session och fortsatt funktion för en
opåverkad låntagare. Den mäter inte nätverksprestanda eller minnesbesparing.

Ej verifierat: två fysiska datorer, swap-integration,
SSD-jämförelse, produktionssäkerhet och prestandavinst. NBD-paketen saknas här.
Fyra integrationstester kördes senare framgångsrikt i GitHub Actions (se nedan). NAS:en behövs inte för kärntester eller lokal demo.

Ingen disk, swapkonfiguration eller brandvägg har ändrats av prototypen.

Regressioner i 0.2.1: skrivskydd i adaptern, immutabel kvotbokföring,
entropifel och tokenkollision före allokering, samt avvisning av symlänkar,
FIFO-filer och för stora nyckelfiler. Alla sju nya tester passerar.

## GitHub Actions — verifierad körning

[Run 40181213266](https://github.com/jojo-swe/memory-bridge/actions/runs/40181213266)
på commit `d8647192d7fbf7306cfa48cb32fdde044433c89d`: **40 av 40 tester godkända, inga överhoppade.**

Ubuntu 24.04, nbdkit 1.36.3, libnbd 1.20.0. Inkluderar riktig TLS-PSK/NBD
på loopback, dataåterläsning, nekad återanslutning, fel nyckel, nekad
okrypterad anslutning och givarprocess som dödas under sessionen.

## Version 0.3.0 — 47 tester

[Run 40181712375](https://github.com/jojo-swe/memory-bridge/actions/runs/40181712375)
på commit `fa43b875c3e3f50a58a1bdb15c982a982f40f6fb`: **47 godkända tester, inga överhoppade.**
Lokalt passerade 43 tester; fyra transporttester kördes i CI. Nya tester täcker
TOML-kvoter i adaptern, annonserad I/O-gräns, ogiltig NBD-blockstorlek,
CLI-konfiguration och dränering med bevarade sessioner.

## Version 0.3.1 — 52 tester

[Run 40182136372](https://github.com/jojo-swe/memory-bridge/actions/runs/40182136372),
commit `bace9c922bd07de1fc2aed27b8e2579b6e69ff0b`: **52 godkända, inga överhoppade.**
Den strikta testkörningen kräver att alla tester körs. Lokalt: 48 godkända
och fyra transporttester överhoppade; strikt läge avvisade korrekt detta resultat.
Regressioner täcker återanvända slumptoken efter frigöring/karantän,
entropifel vid nyckelskapande och startkontroller före nyckelläsning.
