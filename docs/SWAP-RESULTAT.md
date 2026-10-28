# Swap-experiment — observerade resultat och slutsatser

Mätt 2026-10-26 på branchen `feature/swap-experiment-block-export`.
Kriterierna i [SWAP-EXPERIMENT.md](SWAP-EXPERIMENT.md) skrevs och
committades före första mätningen och ändrades inte. Rådata per körning:
[experiments/](experiments/) (JSON, en fil per körning), sammanställd tabell:
[experiments/SAMMANSTALLNING.md](experiments/SAMMANSTALLNING.md), genererad av
`memory-bridge swap-report docs/experiments/swap-*.json`.

## Förutsättningar (observerade)

| | |
| --- | --- |
| Maskin | Disponibel Firecracker-VM, Linux 6.18.44, 2 vCPU, 8 220 244 kB RAM, root |
| Disk | `/dev/vda` virtio-blk (rapporterar `rotational=1`, värdcache okänd), ext4, `/var/tmp`; O_DIRECT 4 KiB-läsning 47–56 µs median i mikrotest |
| Programvara | Python 3.12.3, nbdkit 1.36.3, libnbd 1.20.0, libfuse 2.9.9 (fusepy), util-linux 2.39.3 |
| Kärninställningar | `vm.page-cluster=3` (swap-readahead 8 sidor), cgroup v1 `memory.swappiness=60` |
| Swap | Inget aktivt före; per körning en loop-enhet med `--direct-io=on`, `mkswap`, `swapon -p 100` |
| Minnesbrist | cgroup `memory.limit_in_bytes = 256 MiB`, `memsw.limit = 1024 MiB` runt enbart arbetslasten |
| Arbetslast | `pressure-workload --dataset-mib 512 --rounds 2 --seed 42`, varje sida verifierad |
| Nätverk | loopback (127.0.0.1), TLS-PSK, en givarsession per körning |
| Upprepningar | 3 kompletta roterade upprepningar av sex alternativ, plus 3 extra `bridge`-körningar (ej roterade) efter kodgranskningens fixar |
| Kommandon | Tre anrop av `swap-lab --disposable-vm --directory /var/tmp/swaplab --ram-mib 256 --dataset-mib 512 --swap-mib 768 --rounds 2`: `part1` (`--arms noswap,local-swap,fuse-local,bridge,bridge-sync,more-ram --repetitions 3`, avbrutet efter 7 delkörningar av en labbugg: återanvänt monteringsnamn, rättad i `3c81efa`), `part2` (samma armar, `--repetitions 2 --order-offset 1`), `bridge-postreview` (`--arms bridge --repetitions 3`). Exakta kommandon finns i varje JSON under `environment.command`. |

Avvikelse från designen: alternativet `bridge-sync` (synkron spegling) lades
till som sjätte arm efter att SWAP-EXPERIMENT.md frysts, för attribution av
write-behind; jämförelsen mot det var inte ett förregistrerat kriterium.
Granskningsfixen `a804c56` (läsningar serveras lokalt medan en speglingsbatch
sänds, `mlockall` i NBD-arbetaren) committades medan `part2` pågick, och
labbet startar daemon och arbetare som nya processer per arm: delkörningarna
från upprepning 2 i `part2` (`fuse-local`, `bridge`, `bridge-sync`) använde
därför redan den nya koden. Av de sex `bridge`-körningarna är alltså 2 före
fixen (138,5; 141,8 s) och 4 efter (129,9; 129,0; 118,5; 123,4 s).

## Huvudresultat: uniform åtkomst, 256 MiB RAM, 512 MiB dataset

Random-fasen = `2 × 131072` slumpmässiga sidåtkomster, var åttonde en omskrivning.

| Alternativ | Körningar | Klara | OOM | Verifieringsfel | random-fas median s (min–max) | sidläsning median µs | p99 µs | swap-in/ut (sidor) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| noswap | 3 | 0 | 3 | – | – | – | – | 0/0 |
| local-swap | 4 | 4 | 0 | 0 | **22,6** (21,2–23,8) | 73 | 286 | 272 084 / 344 562 |
| fuse-local | 3 | 3 | 0 | 0 | 73,8 (69,7–73,8) | 200 | 772 | 271 389 / 345 417 |
| bridge (write-behind) | 6 | 6 | 0 | 0 | **129,4** (118,5–141,8) | 268 | 1 242 | 271 578 / 345 604 |
| bridge-sync | 3 | 3 | 0 | 0 | 153,7 (145,9–154,9) | 386 | 1 204 | 272 009 / 345 286 |
| more-ram | 3 | 3 | 0 | 0 | 4,1 (4,0–4,1) | 1 | 3 | 0/0 |

Medianen för enbart de fyra `bridge`-körningarna efter fixen är 126,2 s
(5,6× lokal swap). Totala tider inklusive fill/sweep (median): local-swap
31,7 s, fuse-local 90,2 s, bridge 156,0 s, bridge-sync 191,5 s.

Samtidig minnesåtgång (toppar under körning, `VmHWM`/`VmRSS`):

| Process | bridge |
| --- | --- |
| Arbetslast (i cgroupen) | 265 MiB |
| FUSE-daemon (export) | 102–118 MiB |
| nbdkit-givare (768 MiB låst + ≈ 24 MiB Python/nbdkit) | 792 MiB |
| NBD-arbetare (efter `mlockall`) | 69 MiB (26 MiB utan `mlockall` i de första körningarna) |
| Bridge-processer samtidigt (export+givare+arbetare) | 967–972 MiB (median 971) |

I `part1`/`part2`-filerna räknade monitorn felaktigt in arbetslasten i
`bridge_peak_concurrent_rss_kib`; det är rättat och värdet ovan kommer från
`swap-uniform-bridge-postreview.json`. Cachen täckte 94–99 k av 131 k block
vid slutet; 41–47 k av ≈ 345 k blockskrivningar (12–14 %) lämnades olagrade
för att write-behind-budgeten (16 MiB) var full (`mirror_dropped_blocks`
räknar skrivhändelser, inte unika block).

## Hotset (80 % av åtkomsterna mot 20 % av sidorna), n = 2 per alternativ

| Alternativ | Körningar | random-fas median s | sidläsning p99 µs | swap-in/ut |
| --- | --- | --- | --- | --- |
| local-swap | 2 | 12,3 | 222 | 187 902 / 258 386 |
| fuse-local | 2 | 28,7 | 434 | 187 700 / 258 098 |
| bridge | 2 | 48,2 | 766 | 188 054 / 258 184 |

Medianlatensen för en sidläsning är 1–2 µs i alla alternativ eftersom de flesta
träffar är i RAM; skillnaden syns i p99 och total tid.

## Injicerade givarfel under aktivt swap (bridge, full storlek, en körning per feltyp)

| Fel | Injicerat efter s | Fallback efter s | Verifieringsfel | Max sidläsning ms | remote_enabled efteråt |
| --- | --- | --- | --- | --- | --- |
| frozen (SIGSTOP nbdkit) | 29,3 | 2,04 | 0 | 65 | false |
| dead (SIGKILL nbdkit) | 30,5 | 0,11 | 0 | 79 | false |
| socket (`ss -K` på TCP-anslutningen) | 30,9 | 0,07 | 0 | 70 | false |

Arbetslasten slutförde alla tre körningarna med 0 fel. Vid fryst givare
observerades fallback efter 2,04 s (io-timeout 2,0 s, pollning var 50 ms), men
den längsta enskilda sidläsningen i arbetslasten var 65 ms. Tolkning: väntan
låg i speglingstråden medan läsningar serverades lokalt (den vägen är inte
separat instrumenterad). Ingen återanslutning skedde; `remote_errors=1` i alla
tre. "Max sidläsning" är hela körningens maximum, inte ett fönster runt felet.

## Känslighet: emulerad långsam lokal disk (endast FUSE-armarna)

`--disk-latency-ms` lägger en `time.sleep` före varje lokal I/O i
`DirectFileStorage` i FUSE-daemonen, lika i båda armarna: en sömn per anrop
oavsett storlek, serialiserad i Python. Det är en latensmodell, inte en
långsam disk, och `local-swap` (kernel-swap på vanlig fil) kan inte få samma
knapp, så jämförelsen här är enbart `bridge` mot `fuse-local`. Mindre
storlekar än huvudexperimentet för rimlig körtid; n = 2 per arm.

| Emulerad latens | Storlek | fuse-local median s | bridge median s | sidläsning median µs (fuse-local / bridge) |
| --- | --- | --- | --- | --- |
| +1 ms | 128/256/384 MiB, 1 varv | 107,3 | 66,7 | 1 435 / 404 |
| +5 ms | 64/128/192 MiB, 1 varv | 193,7 | 80,6 | 5 689 / 403 |

## Attribution av kostnaden (mikrotest på loopback, 4 KiB)

Skript: [experiments/latency_probe.py](experiments/latency_probe.py), rådata:
[experiments/microbench-latency.txt](experiments/microbench-latency.txt)
(n = 2000 per rad, körd på commit `5304552` efter huvudexperimentet).

| Steg | Median µs | p99 µs |
| --- | --- | --- |
| O_DIRECT-läsning från `/dev/vda` (`DirectFileStorage`) | 56 | 130 |
| socketpair-eko mellan två Python-processer (ingen NBD) | 76 | 197 |
| libnbd → nbdkit `memory`-plugin med TLS (golv utan projektets kod) | 101 | 199 |
| libnbd → projektets Python-plugin-givare | 132 | 308 |
| + processhopp till NBD-arbetaren (`RemoteNBDStorage`) | 235 | 392 |
| + SHA-256 och lagerlogik (`ResilientStorage`, cachad läsning) | 240 | 353 |
| Synkron skrivning lokal+spegling (`ResilientStorage.write`) | 438 | 878 |

Processhoppet (+100 µs) är till största delen schemaläggning av två
Python-processer, inte kopiering. I själva experimentet var medianen per
sidläsning 268 µs (bridge) mot 73 µs (local-swap); sidläsningar inkluderar
swap-readahead (`vm.page-cluster=3`), inte bara 4 KiB.

## Slutsatser (tolkning, skilt från observationerna ovan)

- **K1 korrekthet: uppfyllt.** 0 verifieringsfel i 30 slutförda swap-körningar
  och i alla tre felinjektionerna; ingen OOM i något swap-alternativ; `noswap`
  OOM-dödades varje gång, så arbetslasten överskred verkligen budgeten.
- **K2 nytta: inte uppfyllt.** Med lokal swap på den här disken är Memory
  Bridge 5,7× långsammare (129,4 s mot 22,6 s; 5,6× med enbart körningarna
  efter fixen) och 1,75× långsammare än samma Python/FUSE-väg utan givare.
  Intervallen överlappar inte. Designens krav på tre roterade upprepningar
  per ordning uppfylldes bara delvis (två av de roterade `bridge`-körningarna
  använde koden före fixen, de tre extra var oroterade), men ett negativt
  utfall av den storleken kräver inte full design. Den sannolika orsaken är
  att en cachad fjärrläsning kostar ≈ 240–340 µs mot ≈ 50–75 µs för en
  direkt diskläsning (mikrotest respektive experiment), och att swap-I/O är
  små anrop.
- **K3 attribution.** Python/FUSE-vägen i sig kostar ≈ 3,3× mot lokal swap
  (73,8 mot 22,6 s). Fjärrcachen lägger ytterligare ≈ 1,75× ovanpå det.
  Write-behind-speglingen minskade kostnaden med 16 % (153,7 → 129,4 s) men
  vänder inte bilden.
- **K4 feltolerans: uppfyllt** mot kriteriet ≤ io-timeout + 1 s: fallback
  efter 2,04 s (fryst), 0,11 s (död), 0,07 s (dödad anslutning); ingen
  datakorruption, ingen tyst återanslutning, längsta sidåtkomst i arbetslasten
  < 0,1 s. En körning per feltyp.
- **K5 klientomstart: uppfyllt på blocknivå**, inte under aktivt swap.
  `test_persistent_backing_survives_daemon_sigkill_and_restart` skriver via
  FUSE-filen, anropar `fsync`, SIGKILL:ar daemonen, startar om den på samma
  SQLite-databas och läser tillbaka korrekt; osynkade skrivningar täcks inte. Omstart av daemonen medan kärnan swappar mot enheten
  är inte testad: ett swapblock som försvinner kan inte göras säkert i
  efterhand, och ett sådant test riskerar själva labb-VM:en.
- **K6 minnesärlighet.** Låntagaren behöver fortfarande full lokal kopia på
  disk (768 MiB) och bridge-processerna tog ≈ 970 MiB samtidigt, varav 768 MiB
  är givarens låsta budget och ≈ 200 MiB är Python-processer. Inget RAM sparas
  på låntagarsidan av detta experiment.
- **Brytpunkt (tolkning, interpolerad mellan två mätpunkter).** Observerat:
  med +1 ms emulerad sömn per lokal I/O var `bridge` 1,6× snabbare än
  `fuse-local`, med +5 ms 2,4× (n = 2, intervallet 1,9–3,1×; mindre
  storlekar; loopback; jämfört med samma Python/FUSE-väg, inte med
  kernel-swap på vanlig fil). Vid 0 ms var `bridge` 1,75× långsammare.
  Tolkningen att fjärrcachen lönar sig först när en lokal 4 KiB-läsning kostar
  mer än ≈ 0,3–0,4 ms (kostnaden för en verifierad fjärrläsning på loopback)
  är en interpolation; den motsvarar HDD-, SD-kort- eller eMMC-klass snarare
  än SSD och måste bekräftas på riktig lagring över LAN, där nätverkslatens
  tillkommer och loopback-kostnaden är en nedre gräns.

## Vad resultaten inte säger

- Inget om fysiskt LAN, Wi-Fi eller två datorer.
- Inget om vilken disk `/dev/vda` egentligen är: 47–73 µs per 4 KiB-läsning
  är SSD- eller värdcache-klass, trots `rotational=1`. Även 100–150 µs skulle
  inte vända jämförelsen enligt mikrotestet, men det är en extrapolation.
- Inget om global minnesbrist: trycket var begränsat till arbetslastens cgroup;
  deadlockrisken för en Python-daemon i swapvägen kvarstår (se SWAP-EXPERIMENT.md).
- Inget om beständighet vid strömavbrott.
- Resultatet gäller den här arkitekturen (tre Python-processer i läsvägen).
  En kärn- eller C-adapter skulle ta bort FUSE-/processhoppen men inte
  nätverksvändan.
