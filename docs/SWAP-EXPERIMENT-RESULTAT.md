# Swap-experiment 0.11 – resultat

Körda 2026-10-26 i sessionens disponibla VM. Kriterier och trösklar fastställdes
före mätning i [SWAP-EXPERIMENT-KRITERIER.md](SWAP-EXPERIMENT-KRITERIER.md)
(commit `11fce8c`). Detta dokument skiljer på **observationer** (mätvärden)
och **slutsatser** (vår tolkning). Rådata: `experiments/swap/results/` i
repot (`2026-10-26/exp1` och `exp1-w1`: `runs.jsonl`, `plan.json`, `environment.json`,
`summary.md`, harness-loggar, kalibreringsposter, per-körningsloggar och 4 Hz-
minnessampel för omgång 1 och deadlock-försöken).

## Kort svar

**Användarnytta uteblev i denna miljö.** Under verklig cgroup-minnesbrist
slutförde den syntetiska sidarbetslasten (W2) 88 % färre begäranden med
Memory Bridge som swap (R) än med en vanlig lokal swapfil (L), med 0 datafel.
Redan vägen med enbart lokal kopia (P) var 4,5× långsammare än kärnans
egen swap: flaskhalsen är användarrymdsvägen (FUSE → NBD → Python), inte
givaren. Felinjektionerna gav 0 datafel och begränsad väntetid (≤ 2,0 s).
Stressvarianten, där adaptern delar minnestryck med arbetslasten, låste sig
(deadlock) två gånger av två. xz-arbetslasten (W1) tog 9,3× så lång tid med
R som med L (1 635 s mot 175 s), med korrekt utdata.

## Miljö (environment.json)

- Firecracker-mikro-VM, Linux 6.18.44, Ubuntu 24.04.5, 2 vCPU, 8 GiB RAM,
  ext4 på virtio-disk (`/dev/vda`). Ingen KVM, ingen `nbd`-/`ublk`-modul.
- nbdkit 1.36.3, libnbd 1.20.0, nbdfuse, fuse3, xz 5.4.5, gcc 13.3.
- Python 3.12.3 (`/usr/bin/python3.12`, matchar libnbd-bindningen).
- zswap av, THP `madvise`, swappiness 60, page-cluster 3.
- Givaren kördes som opriviligierad användare med enbart CAP_IPC_LOCK;
  adapter/nbdfuse/loop som root (krävs för swapon/losetup).
- Repo-commit `d8791f7`. Blockenhet: loop (direct-io, 4096 B sektor) →
  nbdfuse → Unix-socket → `memory-bridge export`.

Kommandon:

```bash
MB_DISPOSABLE_VM=1 /usr/bin/python3.12 experiments/swap/run_experiment.py \
  --i-am-in-a-disposable-vm --out /srv/mbx/exp1 --rounds 5 --rounds-r1 3 \
  --faults all --fault-reps 3 --stress-reps 2 --w1-input-mib 24 --timeout-s 1800
MB_DISPOSABLE_VM=1 /usr/bin/python3.12 experiments/swap/run_experiment.py \
  --i-am-in-a-disposable-vm --out /srv/mbx/exp1-w1 --rounds 5 --rounds-r1 3 \
  --workloads w1 --w1-input-mib 36 --timeout-s 1800
/usr/bin/python3.12 experiments/swap/analyze_results.py /srv/mbx/exp1
```

Budget per körning: cgroup v1 `memory.limit_in_bytes` 256 MiB (Ref 1024 MiB),
1 GiB swap, samma seed (20261025), `drop_caches` och `swapoff` mellan
körningar, slumpad ordning per omgång (sparad i `plan.json`).

## Avvikelser från planen (redovisade)

1. **Kalibrering av W1.** Med 48 MiB indata tog `xz -8` 412 s under L och
   > 900 s under P (tidsgräns). Enligt kriteriedokumentets kalibreringsregel
   minskades indatan. Första valet, 24 MiB, visade sig **ogiltigt**: xz krymper
   ordboken till filstorleken, så minnesbehovet föll under 256 MiB och ingen
   swap uppstod (grind 1 föll; N slutfördes). Den serien (`exp1`, W1) räknas
   inte. Omkörning med 36 MiB (ordboken når sina 32 MiB, topp-RSS ≈ 325 MiB)
   och tidsgräns 1800 s ligger i `exp1-w1`. Trösklarna är oförändrade.
2. **Stressvariant S kördes en gång, inte två.** Båda försöken (ett vid
   första körningen och ett vid återupptagningen) gav samma deadlock och
   krävde manuell återställning av VM:en (FUSE-abort). Loggar för båda finns
   som `s1-deadlocked-attempt1/2`; harness-posten saknas eftersom harnessen
   avbröts under den hängande nedmonteringen.
3. Harnessen avbröts av sessionens 2-timmarsgräns för bakgrundsjobb och
   återupptogs (den hoppar över färdiga körningar). Inga mätvärden berörs.
4. **W1 kördes 3 omgångar, inte 5, och R1 2 gånger.** En omgång tar ≈ 50
   min (R ensam 27 min, R1 30 min till timeout). Med ett gap på 9× och
   spridning på ± 3 % ändrar två omgångar till inte utfallet; kriteriets
   "4 av 5" blir "3 av 3" i samma riktning. Detta är en avvikelse från
   förregistreringen och redovisas som sådan.


## W2 – pagestress (512 MiB data, 60 s, 256 MiB budget)

| Konfig | n | status | begäranden median (min–max) | p50 ms | p99 ms | p99.9 ms | max ms | swap-in/ut sidor (median) | datafel | adapter-RSS MiB | givar-RSS MiB | adapter-CPU s / 60 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| N | 5 | OOM-kill ×5 | – | – | – | – | – | 0/0 | – | – | – | – |
| L | 5 | ok | 861 704 (799 244–877 631) | 0,063 | 0,36 | 0,8 | 32 | 818 892 / 233 430 | 0 | – | – | – |
| P | 5 | ok | 189 668 (183 462–193 509) | 0,276 | 1,80 | 5,6 | 102 | 282 282 / 164 711 | 0 | 39 | – | 53 |
| R | 5 | ok | 103 601 (95 239–105 915) | 0,471 | 3,11 | 10,3 | 262 | 209 204 / 154 955 | 0 | 74 | 1 048 | 72 (+19 givare) |
| R1 | 3 | ok | 21 265 (20 105–22 210) | 1,830 | 10,35 | 20,0 | 405 | 137 073 / 130 954 | 0 | 74 | 1 048 | 43 (+15 givare) |
| Ref | 5 | ok | 4 673 652 (4 529 484–4 744 505) | 0,012 | 0,04 | 0,1 | 6 | 0/0 | 0 | – | – | – |

Observationer:

- Populeringsfasen (512 MiB sekventiellt): L 0,8 s, P 1,9 s, R 5,7 s, R1 9,0 s.
- I R kom 773–820 MiB av läsningarna från givaren och 1 MiB från lokal kopia:
  cachen träffar nästan alltid. Den är ändå långsammare än P:s lokala O_DIRECT-
  läsningar (1 067–1 110 MiB).
- CPU: R förbrukade ≈ 90 s CPU (adapter 35, worker 21, nbdfuse 15, givare 19)
  på 60 s väggtid med 2 vCPU, samtidigt som arbetslasten fick 9,8 s (L: 39 s).
- Systemets använda minne vid topp: L 856 MiB, P 932 MiB, R 1 958 MiB
  (givarens 1 GiB låsta RAM + adapter). På en dator är det absurt; på två
  datorer flyttas minnet men totalbehovet växer.
- Inga datafel i någon körning (verifiering per sida + slutsvep).
- R var sämre än L i 5 av 5 parade omgångar oavsett position i ordningen.

Slutsats (W2): kriterium 1 ej uppfyllt (−88 % i stället för +10 %), p99 8,6×
L:s. Adapterns samtidiga topp-RSS 74 MiB överskrider kriteriets 64 MiB
(kriterium 4). R1 (1 ms per begäran) är ytterligare 5× långsammare: på
riktigt LAN/Wi-Fi blir gapet större, inte mindre.

## W1 – xz -8 -T1 (36 MiB deterministisk indata, SHA-256-verifierad utdata)

Serie `exp1-w1`, 3 omgångar (avvikelse 4). Utan minnesgräns tar jobbet
≈ 47 s och når ≈ 325 MiB RSS.

| Konfig | n | status | jobbtid s median (min–max) | swap-in/ut sidor (median) | utdata korrekt | adapter-RSS MiB | givar-RSS MiB | adapter-CPU s per jobb |
|---|---|---|---|---|---|---|---|---|
| N | 3 | OOM-kill ×3 | – | 0/0 | – | – | – | – |
| L | 3 | ok | 175 (164–181) | 1 553 191 / 1 576 328 | 3/3 | – | – | – |
| P | 3 | ok | 855 (798–858) | 1 572 050 / 1 633 501 | 3/3 | 39 | – | 604 |
| R | 3 | ok | 1 635 (1 612–1 691) | 1 545 381 / 1 610 767 | 3/3 | 74 | 1 048 | 1 175 |
| R1 | 2 | timeout > 1 800 s ×2 | – | 495 530 / 527 956 (vid avbrott) | – | 74 | 1 048 | 453 |
| Ref | 3 | ok | 47,6 (45,6–49,0) | 0/0 | 3/3 | – | – | – |

Observationer: alla konfigurationer swappade ungefär lika många sidor
(≈ 1,55 M in / 1,6 M ut, dvs. ≈ 6 GiB i vardera riktningen); skillnaden är
kostnaden per sida. L: 175 s. P: 4,9× L. R: 9,3× L, med 1 175 s adapter-CPU
per jobb. R1 blev inte klart på 30 min (≈ 1/3 av sidorna på den tiden).
Utdatans SHA-256 stämde i varje slutförd körning.

Slutsats (W1): kriterium 1 ej uppfyllt; R förlorade 3 av 3 parade omgångar.

## Felinjektion (R, W2, fel 20 s in i begärandefasen, fjärr-timeout 2 s)

| Fel | n | slutfört | datafel | max svarstid i felfönstret | fjärrcache avstängd efteråt | begäranden (median) |
|---|---|---|---|---|---|---|
| F1 fryst givare (SIGSTOP) | 3 | 3/3 | 0 | 2 006–2 008 ms | ja (1 fjärrfel) | 155 325 |
| F2 död givare (SIGKILL) | 3 | 3/3 | 0 | 118–283 ms | ja | 169 027 |
| F3 tappat nätverk (nftables drop) | 3 | 3/3 | 0 | 2 006–2 011 ms | ja | 158 814 |
| F4 adaptern dödas (SIGKILL) | 3 | 0/3 | – | – | – | arbetslasten dog (SIGSEGV) 5 s efter felet |

Observationer: fryst givare och tappat nät kostar exakt en deadline (2 s)
varefter all I/O går lokalt; död givare upptäcks via TCP-reset inom 0,3 s.
Jobben slutförde fler begäranden än R utan fel (lokal väg är snabbare).
F4: när adaptern försvinner får kärnan läsfel på swap och dödar processen
– ett synligt fel, ingen tyst korruption. F4b (omstart av adapter mot samma
namngivna kopia läser tillbaka kvitterade skrivningar) är verifierad i
`tests/test_block_export.py`, inte i harnessen.

Slutsats: F1–F3 godkända enligt kriterierna (≤ 4 s, 0 datafel, fallback
synlig i statistiken). F4 ger det tillåtna "misslyckas synligt".

## Stressvariant S (adapter, worker och nbdfuse i arbetslastens cgroup)

Två av två försök låste sig inom 1 s efter att cgroupen nått 256 MiB:
swap-ut stannade vid 12,7 respektive 20,7 MiB, nbdfuse-trådarna hamnade i
D-tillstånd (`INFO: task nbdfuse blocked for more than 121 seconds`),
cgroupens PSI full → 99 % och arbetslasten fastnade i `rq_qos_wait`.
Ingen OOM-kill utlöstes. `swapoff` hängde i `folio_wait_bit`; VM:en kunde
bara återställas genom att abortera FUSE-anslutningen
(`/sys/fs/fuse/connections/N/abort`), vilket gav I/O-fel på swap.

Slutsats: detta är den rekursiva swap-/allokeringsdeadlock som kriterie-
dokumentet varnade för. `mlockall(MCL_ONFAULT)` hindrar att adaptern swappas
ut men inte att dess sidfel och allokeringar väntar på återvinning som i
sin tur väntar på adaptern. `PR_SET_IO_FLUSHER` kunde inte sättas (saknar
CAP_SYS_RESOURCE i VM:en) och hade bara minskat, inte tagit bort, risken.
På en riktig dator med global RAM-brist är detta det normala läget, inte
undantaget: huvudseriens resultat för P/R är därför *optimistiska*.

## Fel och flaskhalsar som åtgärdats under arbetet

- `mlockall` utan `MCL_ONFAULT` gjorde adaptern 250 MiB resident (hela
  adressrymden förfaultad); nu ~28–39 MiB.
- Per-chunk-deadlines lät en långsam givare hålla en begäran N × timeout;
  nu en gemensam deadline per begäran.
- Givarens memlock-förkontroll ignorerade CAP_IPC_LOCK.
- Inbäddad nbdkit-Python rapporterar fel `sys.executable`; workern startas
  nu med explicit tolk.
- Statsfilen följde symlänkar; tmpfs accepterades som "disk"; skrivningar
  < 4 KiB tillät icke-atomär read-modify-write. Alla rättade.

## Vad som inte visats

Fysiskt LAN, kärnans `nbd`-modul, global RAM-brist för hela datorn,
strömavbrott, andra operativsystem, riktiga konsumentprogram utöver xz.
Ingen del av detta säger något om användarnytta på två fysiska datorer –
men riktningen (R1 ≫ R i kostnad) talar emot att nätet skulle hjälpa.

## Beslutsgrind

Enligt NULAGE-OCH-NASTA-STEG.md: *"Om vinsten uteblir: analysera var
kostnaden ligger och pröva en ändrad arkitektur eller smalare målgrupp."*
Kostnaden ligger (1) i användarrymdsvägen per 4 KiB-sida (P är 4,5× L
utan något nätverk alls) och (2) i att varje lånad sida ändå kräver en
lokal kopia, så disken aldrig avlastas. Nästa steg bör därför inte vara
mer funktion, utan en kärnnära väg (nbd-modul med stora köer, eller en
utvärdering av om målet "mer RAM" alls går att nå med en swap-arkitektur
jämfört med att kompensera med zram/zswap lokalt).
