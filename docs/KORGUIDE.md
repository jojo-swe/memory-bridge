# Memory Bridge 0.8 – körguide

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

## Blockexport och swap-experiment i 0.11

**Kör aldrig detta på din vardagsdator.** `swap-lab` skapar swap, loop-enheter,
FUSE-monteringar och cgroups och vägrar starta om swap redan är aktivt, utan
root eller utan flaggan `--disposable-vm`. Använd en VM du kan kasta.

Beroenden utöver NBD-paketen:

```bash
sudo apt-get install fuse3 libfuse2t64 iproute2
/usr/bin/python3 -m pip install --break-system-packages fusepy
```

Enbart blockexport (ingen swap, ingen root behövs för själva monteringen):

```bash
mkdir /tmp/mb-mount
/usr/bin/python3 -m memory_bridge block-export /tmp/mb-mount --size-mib 64 --directory /var/tmp
# i en annan terminal: filen /tmp/mb-mount/volume är lagret, /tmp/mb-mount/stats.json visar räknare
fusermount3 -u /tmp/mb-mount
```

Lägg till `--key session.psk --host GIVARE --port 10809` för att spegla till en
ny givare; `--write-behind-mib 0` ger synkron spegling, standard är 16 MiB
asynkron budget. `--backing persistent --path lager.db --create` ger den
beständiga SQLite-bakändan.

Hela experimentet, som root i en disponibel VM:

```bash
sudo /usr/bin/python3 -m memory_bridge swap-lab --disposable-vm \
  --arms noswap,local-swap,fuse-local,bridge,bridge-sync,more-ram \
  --repetitions 3 --ram-mib 256 --dataset-mib 512 --swap-mib 768 --rounds 2 \
  --directory /var/tmp --output resultat.json
```

Körordningen roteras per upprepning. `--pattern hotset` ger 80/20-lokalitet,
`--fault frozen|dead|socket` injicerar ett givarfel i `bridge`-armen när den
slumpmässiga fasen har startat, och `--disk-latency-ms` lägger emulerad
disklatens på FUSE-armarna (endast för att hitta brytpunkten mot lokal disk;
kombinera inte med `local-swap`). Rådata skrivs som JSON efter varje delkörning.
Allt städas före rapporten; kontrollera ändå `/proc/swaps`, `losetup -l` och
`/sys/fs/cgroup/memory` efteråt. `memory-bridge swap-report fil1.json fil2.json`
sammanställer en eller flera rapporter till Markdown-tabeller. Resultat och
tolkning: [SWAP-RESULTAT.md](SWAP-RESULTAT.md).
