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
| Disk | `/dev/vda` virtio-blk, ext4, `/var/tmp`; O_DIRECT 4 KiB-läsning ≈ 47 µs median i mikrotest |
| Programvara | Python 3.12.3, nbdkit 1.36.3, libnbd 1.20.0, libfuse 2.9.9 (fusepy), util-linux 2.39.3 |
| Kärninställningar | `vm.page-cluster=3` (swap-readahead 8 sidor), cgroup v1 `memory.swappiness=60` |
| Swap | Inget aktivt före; per körning en loop-enhet med `--direct-io=on`, `mkswap`, `swapon -p 100` |
| Minnesbrist | cgroup `memory.limit_in_bytes = 256 MiB`, `memsw.limit = 1024 MiB` runt enbart arbetslasten |
| Arbetslast | `pressure-workload --dataset-mib 512 --rounds 2 --seed 42`, varje sida verifierad |
| Nätverk | loopback (127.0.0.1), TLS-PSK, en givarsession per körning |
| Upprepningar | 3 per alternativ, körordningen roterad per upprepning; `bridge` kördes 6 gånger (3 före och 3 efter kodgranskningens fixar) |
| Kommando | `swap-lab --disposable-vm --arms noswap,local-swap,fuse-local,bridge,bridge-sync,more-ram --repetitions 3 --ram-mib 256 --dataset-mib 512 --swap-mib 768 --rounds 2 --directory /var/tmp/swaplab` |

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

De tre `bridge`-körningarna efter granskningsfixen (läsningar serveras lokalt
medan en speglingsbatch är under sändning) gav 118,5 / 123,4 / 129,0 s mot
138,5 / 141,8 / 129,9 s före. Totala tider inklusive fill/sweep: local-swap
≈ 31 s, fuse-local ≈ 88 s, bridge ≈ 155 s, bridge-sync ≈ 190 s.

Samtidig minnesåtgång (toppar under körning, `VmHWM`/`VmRSS`):

| Process | bridge |
| --- | --- |
| Arbetslast (i cgroupen) | 265 MiB |
| FUSE-daemon (export) | 106–119 MiB |
| nbdkit-givare (768 MiB låst + Python) | 792 MiB |
| NBD-arbetare (efter `mlockall`) | 69 MiB (26 MiB utan `mlockall` i de första körningarna) |
| Bridge-processer samtidigt (export+givare+arbetare) | ≈ 967 MiB |

I `part1`/`part2`-filerna räknade monitorn felaktigt in arbetslasten i
`bridge_peak_concurrent_rss_kib`; det är rättat och värdet ovan kommer från
`swap-uniform-bridge-postreview.json`. Cachen täckte 94–99 k av 131 k block;
41–47 k blockskrivningar lämnades olagrade för att write-behind-budgeten
(16 MiB) var full (`mirror_dropped_blocks`).

## Hotset (80 % av åtkomsterna mot 20 % av sidorna)

| Alternativ | Körningar | random-fas median s | sidläsning p99 µs | swap-in/ut |
| --- | --- | --- | --- | --- |
| local-swap | 2 | 12,3 | 222 | 187 902 / 258 386 |
| fuse-local | 2 | 28,7 | 434 | 187 700 / 258 098 |
| bridge | 2 | 48,2 | 766 | 188 054 / 258 184 |

Medianlatensen för en sidläsning är 1–2 µs i alla alternativ eftersom de flesta
träffar är i RAM; skillnaden syns i p99 och total tid.

## Injicerade givarfel under aktivt swap (bridge, full storlek)

| Fel | Injicerat efter s | Fallback efter s | Verifieringsfel | Max sidläsning ms | remote_enabled efteråt |
| --- | --- | --- | --- | --- | --- |
| frozen (SIGSTOP nbdkit) | 29,3 | 2,04 | 0 | 65 | false |
| dead (SIGKILL nbdkit) | 30,5 | 0,11 | 0 | 79 | false |
| socket (`ss -K` på TCP-anslutningen) | 30,9 | 0,07 | 0 | 70 | false |

Arbetslasten slutförde alla tre körningarna med 0 fel. Vid fryst givare tog det
exakt en I/O-deadline (2,0 s) innan cachen övergavs, men den längsta enskilda
sidläsningen var 65 ms: väntan låg i speglingstråden medan läsningar serverades
lokalt. Ingen återanslutning skedde; `remote_errors=1` i alla tre.

## Känslighet: emulerad långsam lokal disk (endast FUSE-armarna)

`--disk-latency-ms` lägger en sömn före varje lokal I/O i `DirectFileStorage`,
lika i båda armarna. Mindre storlekar för rimlig körtid.

| Emulerad latens | Storlek | fuse-local median s | bridge median s | sidläsning median µs (fuse-local / bridge) |
| --- | --- | --- | --- | --- |
| +1 ms | 128/256/384 MiB, 1 varv | 107,3 | 66,7 | 1 435 / 404 |
| +5 ms | 64/128/192 MiB, 1 varv | 193,7 | 80,6 | 5 689 / 403 |

## Attribution av kostnaden (mikrotest på loopback, 4 KiB)

| Steg | Median µs |
| --- | --- |
| O_DIRECT-läsning från `/dev/vda` | 47 |
| libnbd → nbdkit `memory`-plugin med TLS (golv utan projektets kod) | 100 |
| libnbd → projektets Python-plugin-givare | 122 |
| + processhopp till NBD-arbetaren (`RemoteNBDStorage`) | 270 |
| + SHA-256 och lagerlogik (`ResilientStorage`, cachad läsning) | 338 |
| Synkron skrivning lokal+spegling (`ResilientStorage.write`) | 411 |

Ett rent socketpair-ekotest mellan två Python-processer kostar 87 µs på den
här VM:en; processhoppet är alltså till stor del schemaläggning, inte kopiering.

## Slutsatser (tolkning, skilt från observationerna ovan)

- **K1 korrekthet: uppfyllt.** 0 verifieringsfel i 30 slutförda swap-körningar
  och i alla tre felinjektionerna; ingen OOM i något swap-alternativ; `noswap`
  OOM-dödades varje gång, så arbetslasten överskred verkligen budgeten.
- **K2 nytta: inte uppfyllt.** Med lokal swap på den här disken är Memory
  Bridge 5,7× långsammare (129 s mot 22,6 s) och 1,75× långsammare än samma
  Python/FUSE-väg utan givare. Spridningen är liten; resultatet är inte ett
  mätfel. Orsaken är att en cachad fjärrläsning kostar ≈ 340 µs mot ≈ 50 µs
  för en direkt diskläsning, och att swap-I/O är 4 KiB-dominerad.
- **K3 attribution.** Python/FUSE-vägen i sig kostar ≈ 3,3× mot lokal swap
  (73,8 mot 22,6 s). Fjärrcachen lägger ytterligare ≈ 1,75× ovanpå det.
  Write-behind-speglingen minskade kostnaden med 16 % (153,7 → 129,4 s) men
  vänder inte bilden.
- **K4 feltolerans: uppfyllt.** Fallback ≤ io-timeout, ingen datakorruption,
  ingen tyst återanslutning, längsta väntetid för arbetslasten < 0,1 s.
- **K5 klientomstart: uppfyllt på blocknivå**, inte under aktivt swap.
  `test_persistent_backing_survives_daemon_sigkill_and_restart` skriver via
  FUSE-filen, SIGKILL:ar daemonen, startar om den på samma SQLite-databas och
  läser tillbaka korrekt. Omstart av daemonen medan kärnan swappar mot enheten
  är inte testad: ett swapblock som försvinner kan inte göras säkert i
  efterhand, och ett sådant test riskerar själva labb-VM:en.
- **K6 minnesärlighet.** Låntagaren behöver fortfarande full lokal kopia på
  disk (768 MiB) och bridge-processerna tog ≈ 967 MiB samtidigt, varav 768 MiB
  är givarens låsta budget och ≈ 200 MiB är Python-processer. Inget RAM sparas
  på låntagarsidan av detta experiment.
- **Brytpunkt.** Fjärrcachen lönar sig först när en lokal 4 KiB-läsning kostar
  mer än ≈ 0,4 ms (loopback-kostnaden för en verifierad fjärrläsning): med
  +1 ms emulerad disklatens var bridge 1,6× snabbare än samma väg utan givare,
  med +5 ms 2,4×. Det motsvarar HDD-, SD-kort- eller eMMC-klass, inte SSD.
  Över ett riktigt LAN tillkommer dessutom nätverkslatens; den uppmätta
  loopback-kostnaden är en nedre gräns för fjärrvägen.

## Vad resultaten inte säger

- Inget om fysiskt LAN, Wi-Fi eller två datorer.
- Inget om värd-cache i `/dev/vda`: diskläsningarna kan vara snabbare här än på
  en vanlig bärbar SSD, men även 100–150 µs skulle inte vända jämförelsen.
- Inget om global minnesbrist: trycket var begränsat till arbetslastens cgroup;
  deadlockrisken för en Python-daemon i swapvägen kvarstår (se SWAP-EXPERIMENT.md).
- Inget om beständighet vid strömavbrott.
- Resultatet gäller den här arkitekturen (tre Python-processer i läsvägen).
  En kärn- eller C-adapter skulle ta bort FUSE-/processhoppen men inte
  nätverksvändan.
