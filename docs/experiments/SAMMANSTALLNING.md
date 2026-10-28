
### hotset, emulerad disklatens 0.0 ms, RAM 256 MiB, dataset 512 MiB, swap 768 MiB, 2 varv

| Alternativ | Körningar | Klara | OOM | Verifieringsfel | random-fas median s (min–max) | sidläsning median µs | p99 µs | max ms | swap-in/ut median | bridge-RSS samtidig MiB | filer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| local-swap | 2 | 2 | 0 | 0 | 12.3 (12.0–12.6) | 2 | 222 | 42.1 | 187902/258386 | – | swap-hotset.json |
| fuse-local | 2 | 2 | 0 | 0 | 28.7 (27.3–30.1) | 2 | 434 | 84.5 | 187700/258098 | 59 | swap-hotset.json |
| bridge | 2 | 2 | 0 | 0 | 48.2 (46.3–50.1) | 2 | 766 | 69.5 | 188054/258184 | 972 | swap-hotset.json |

### uniform, emulerad disklatens 0.0 ms, RAM 256 MiB, dataset 512 MiB, swap 768 MiB, 2 varv

| Alternativ | Körningar | Klara | OOM | Verifieringsfel | random-fas median s (min–max) | sidläsning median µs | p99 µs | max ms | swap-in/ut median | bridge-RSS samtidig MiB | filer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| noswap | 3 | 0 | 3 | 0 | – (–––) | – | – | – | –/– | – | swap-uniform-part1.json, swap-uniform-part2.json |
| local-swap | 4 | 4 | 0 | 0 | 22.6 (21.2–23.8) | 73 | 286 | 17.8 | 272084/344562 | – | swap-uniform-part1.json, swap-uniform-part2.json |
| fuse-local | 3 | 3 | 0 | 0 | 73.8 (69.7–73.8) | 200 | 772 | 86.0 | 271389/345417 | 334 | swap-uniform-part1.json, swap-uniform-part2.json |
| bridge | 6 | 6 | 0 | 0 | 129.4 (118.5–141.8) | 268 | 1242 | 95.7 | 271578/345604 | 1078 | swap-uniform-bridge-postreview.json, swap-uniform-part1.json, swap-uniform-part2.json |
| bridge-sync | 3 | 3 | 0 | 0 | 153.7 (145.9–154.9) | 386 | 1204 | 151.0 | 272009/345286 | 1143 | swap-uniform-part1.json, swap-uniform-part2.json |
| more-ram | 3 | 3 | 0 | 0 | 4.1 (4.0–4.1) | 1 | 3 | 0.9 | 0/0 | – | swap-uniform-part1.json, swap-uniform-part2.json |

### uniform, emulerad disklatens 1.0 ms, RAM 128 MiB, dataset 256 MiB, swap 384 MiB, 1 varv

| Alternativ | Körningar | Klara | OOM | Verifieringsfel | random-fas median s (min–max) | sidläsning median µs | p99 µs | max ms | swap-in/ut median | bridge-RSS samtidig MiB | filer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fuse-local | 2 | 2 | 0 | 0 | 107.3 (105.0–109.5) | 1435 | 3108 | 238.6 | 102866/138922 | 55 | swap-slowdisk-1ms.json |
| bridge | 2 | 2 | 0 | 0 | 66.7 (61.7–71.7) | 404 | 1794 | 228.2 | 102835/138700 | 570 | swap-slowdisk-1ms.json |

### uniform, emulerad disklatens 5.0 ms, RAM 64 MiB, dataset 128 MiB, swap 192 MiB, 1 varv

| Alternativ | Körningar | Klara | OOM | Verifieringsfel | random-fas median s (min–max) | sidläsning median µs | p99 µs | max ms | swap-in/ut median | bridge-RSS samtidig MiB | filer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fuse-local | 2 | 2 | 0 | 0 | 193.7 (186.3–201.1) | 5689 | 8874 | 1016.6 | 52616/70908 | 55 | swap-slowdisk-5ms.json |
| bridge | 2 | 2 | 0 | 0 | 80.6 (65.8–95.4) | 403 | 5837 | 752.4 | 52723/70682 | 368 | swap-slowdisk-5ms.json |

### Injicerade givarfel (bridge)

| Fel | Injicerat efter s | Fallback efter s | Verifieringsfel | max sidläsning ms | remote_enabled efteråt | fil |
|---|---|---|---|---|---|---|
| dead | 30.5 | 0.11 | 0 | 78.8 | False | swap-fault-dead.json |
| frozen | 29.3 | 2.04 | 0 | 65.3 | False | swap-fault-frozen.json |
| socket | 30.9 | 0.07 | 0 | 69.9 | False | swap-fault-socket.json |
