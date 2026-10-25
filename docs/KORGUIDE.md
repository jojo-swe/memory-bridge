# Memory Bridge 0.11 – körguide

## Vad som är färdigt

En Linux-labbversion: låst givar-RAM, autentiserad TLS/NBD-transport,
lokal skrivgenomgående filkopia, timeout/fallback och verifierad jämförelse.
Detta är inte en färdig produkt som automatiskt ökar RAM i vanliga program.

## Kör utan NAS

Installera systempaket en gång på Ubuntu-testdatorn:

```bash
sudo apt-get install nbdkit nbdkit-plugin-python python3-libnbd
/usr/bin/python3 lab.py --operations 256 --request-kib 64 --memory-limit-mib 128
```

Labbet skapar privata tillfälliga filer och loopback-givare. Ingen publik port,
permanent nyckel, swapenhet eller systeminställning skapas. Alla framgångsrikt
startade resurser städas före lyckad rapport. Avbruten benchmark dödar sin
processgrupp. Ett hårt dödande av själva labbföräldern är inte en garanterad
städväg: använd Ctrl+C eller låt tidsgränserna löpa ut.

Utan NBD-paketen kan lokal fil testas separat:

```bash
python3 compare_benchmark.py --mode local --size-mib 8 --request-kib 64 --memory-limit-mib 64
python3 -m memory_bridge recovery-demo
```

`--directory` väljer en diskbaserad katalog. Tmpfs och operativsystemets filcache
kan annars göra diskresultat missvisande. Labbet rensar inte global filcache.

## Kör över LAN

På givaren, skapa en privat nyckel och starta en ny export:

```bash
/usr/bin/python3 bridge.py keygen session.psk
/usr/bin/python3 bridge.py donor --key session.psk --size-mib 1 --bind 192.168.1.50
```

Använd datorns riktiga LAN-adress. Kopiera nyckeln till låntagaren via en
betrodd kanal, sätt rättigheter `chmod 600 session.psk`. Kör på låntagaren:

```bash
/usr/bin/python3 compare_benchmark.py --host 192.168.1.50 --key session.psk --size-mib 1 --request-kib 64
```

Givaren är avsiktligt engångsbaserad: starta om den före varje försök.
`--size-mib` måste matcha exporten exakt. Begränsa eventuell brandväggsöppning
till låntagaren; publicera inte exporten mot internet.

## Prova avbrott

Starta en ny givare. Kör följande och stäng givaren när klienten ber om det:

```bash
/usr/bin/python3 recovery_client.py --host 192.168.1.50 --key session.psk --size-mib 1 --pause-for-fault
```

`fallback_observed: true` och `verified: true` visar att testdata kunde läsas
från filkopian. Testet skriver också efter avbrottet. Det verifierar inte
kraschåterställning: kopian är tillfällig och försvinner när klienten avslutas.

## Tolka mätningen

- `duration_ratios > 1`: fjärrläget var långsammare i just den körningen.
- Median/p95 och IOPS avser lagringsanrop, inte datagenerering/verifiering.
- Skrivningar verifieras med extra läsningar, vilket påverkar cacheläget.
- RSS-toppar för processer är separata, inte samtidiga, och utesluter givaren.
- `--memory-limit-mib` begränsar virtuell adressrymd, inte total fysisk RAM.
- Full lokal filkopia krävs; ingen RAM-besparing eller appacceleration är bevisad.

Kör båda ordningarna flera gånger och spara JSON från varje körning. Ett litet
loopback-test i CI är en funktionskontroll. Slutsatser om användarnytta kräver
representativa arbetslaster, riktig LAN-latens och kontrollerat systemminnestryck.

## Kvar före konsumentprodukt

Fysisk LAN/VM-verifiering, mätbar nytta under RAM-brist, verifiering av beständighet vid strömavbrott,
integritetsskydd mot opålitliga givare, plattformsintegration och användargränssnitt.
Ingen kernel-/swapkoppling ska sättas på en vardagsdator utifrån dessa labbresultat.

## Behåll lokal kopia efter omstart

`checkpoint_demo.py create checkpoint.db` skapar en ny privat databas och
skriver endast syntetiska testdata. `checkpoint_demo.py verify checkpoint.db`
kontrollerar den i en ny process utan nätverk. Filerna är beständiga och måste
raderas av dig när testet är klart. Detta är ett annat backend än labbets
temporära fil och har andra skrivkostnader; jämför inte deras resultat som
om de hade samma beständighetskrav. Fullständiga begränsningar finns i README.

## 0.11: exponera låntagarlagret som blockenhet (endast disponibel VM)

`memory-bridge export` startar nbdkit med återhämtningslagret (lokal O_DIRECT-
kopia först, valfri SHA-256-verifierad givarcache) på en privat Unix-socket.
Det skapar ingen swap och rör ingen enhet; nästa steg gör du själv, och bara
i en VM du kan kasta.

```bash
mkdir -m 700 run
# Lokal kopia i disk-katalogen ./data (tmpfs avvisas), givare på 192.168.1.50
memory-bridge export --socket run/export.sock --size-mib 1024 --local-dir ./data \
    --donor-host 192.168.1.50 --key session.psk --timeout 2 --stats export-stats.json
```

Minneslåsning är på som standard och kräver CAP_IPC_LOCK eller `ulimit -l
unlimited`; `--no-lock-memory` är enbart för tester. `--local PATH --create`
ger en namngiven kopia som överlever adapteromstart (kvitterade skrivningar
läses tillbaka av nästa adapterprocess; cachen räknas då som tom).

Koppla in som swap, i den disponibla VM:en, som root:

```bash
# Kärna med nbd-modul:
nbd-client -unix run/export.sock /dev/nbd0 -block-size 4096
# Kärna utan nbd (som i vårt labb): loop via nbdfuse, direct-io krävs
mkdir fuse && nbdfuse fuse/swapdev --unix run/export.sock &
losetup --direct-io=on --sector-size 4096 --show -f fuse/swapdev   # -> /dev/loopN
mkswap /dev/loopN && swapon /dev/loopN
```

Avveckla i omvänd ordning: `swapoff`, `losetup -d`, `fusermount3 -u`, stäng
adaptern. `export-stats.json` skrivs varje sekund och visar varifrån data
lästs (`remote_read_bytes`/`local_read_bytes`), fjärrfel och fallback.

## Reproducera swap-experimentet

```bash
sudo apt-get install nbdkit nbdkit-plugin-python python3-libnbd libnbd-bin fuse3 gcc xz-utils nftables
sudo MB_DISPOSABLE_VM=1 /usr/bin/python3 experiments/swap/run_experiment.py \
    --i-am-in-a-disposable-vm --out /srv/mbx/exp --rounds 5 --faults all --stress-reps 2
/usr/bin/python3 experiments/swap/analyze_results.py /srv/mbx/exp
```

Harnessen vägrar starta om swap redan är aktivt, om zswap är på, eller utan
båda bekräftelserna. Den städar bara objekt den själv skapat. Rådata:
`plan.json` (ordning fast före första körningen), `environment.json`,
`runs.jsonl`, per körning loggar och `samples.jsonl.gz` (4 Hz minnessampel).
Kriterier och tolkning: [SWAP-EXPERIMENT-KRITERIER.md](SWAP-EXPERIMENT-KRITERIER.md),
resultat: [SWAP-EXPERIMENT-RESULTAT.md](SWAP-EXPERIMENT-RESULTAT.md).
