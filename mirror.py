#!/usr/bin/env python3
"""
AniLiberty Mirror Scanner
Сканирует указанные ID аниме через API AniLiberty и создает:
  - mirrors/anime/<id>/metadata.json
  - mirrors/anime/<id>/episodes.json
  - mirrors/anime/<id>/poster.jpg  (или .webp)
  - mirrors/anime/<id>/playlist.m3u
  - main.m3u (общий плейлист)

Запуск:
    pip install requests
    python mirror.py

В GitHub Actions зависимость requests ставится через pip.
"""

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, List, Any

import requests

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

BASE_URL = "https://aniliberty.top/api/v1"
RELEASE_URL = f"{BASE_URL}/anime/releases"

MIRRORS_DIR = Path("mirrors/anime")
MAIN_M3U = Path("main.m3u")
ERRORS_FILE = Path("errors.json")
LAST_SYNC_FILE = Path(".last_sync")

THREADS = 8
TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

ALL_IDS = [
381, 382, 383, 384, 389, 390, 391, 392, 393, 394, 395, 396, 398, 399, 400, 401, 402, 404, 405, 406, 407, 408, 410, 411, 412, 413, 414, 415, 416, 417, 418, 420, 421, 422, 423, 424, 426, 429, 431, 433, 434, 435, 436, 438, 439, 440, 441, 442, 443, 466, 467, 468, 469, 473, 474, 475, 476, 477, 478, 479, 480, 481, 482, 483, 484, 485, 486, 489, 495, 496, 502, 505, 507, 508, 511, 513, 515, 516, 517, 518, 519, 520, 521, 522, 543, 554, 556, 564, 565, 566, 567, 568, 569, 570, 571, 572, 573, 574, 576, 577, 578, 579, 608, 611, 638, 644, 656, 660, 664, 686, 707, 711, 740, 741, 742, 753, 758, 762, 767, 772, 774, 777, 780, 783, 787, 796, 801, 809, 821, 822, 823, 824, 825, 826, 834, 870, 872, 873, 876, 877, 878, 882, 893, 908, 973, 1068, 1092, 1128, 1130, 1184, 1201, 1202, 1203, 1205, 1206, 1209, 1210, 1211, 1217, 1225, 1229, 1230, 1243, 1247, 1253, 1274, 1286, 1337, 1400, 1405, 1408, 1410, 1454, 1531, 1622, 1641, 1663, 1713, 1744, 1745, 1748, 1749, 1750, 1751, 1752, 1753, 1755, 1756, 1757, 1758, 1759, 1760, 1761, 1762, 1764, 1765, 1772, 1773, 1785, 1807, 1834, 1908, 2086, 2110, 2111, 2112, 2113, 2114, 2115, 2116, 2118, 2120, 2121, 2122, 2124, 2125, 2126, 2127, 2128, 2129, 2131, 2132, 2133, 2135, 2137, 2154, 2233, 2333, 2417, 2495, 2590, 2621, 2622, 2624, 2626, 2628, 2630, 2631, 2632, 2633, 2634, 2635, 2636, 2637, 2639, 2640, 2641, 2642, 2643, 2644, 2645, 2646, 2720, 2766, 2850, 2872, 2919, 2925, 2947, 3024, 3045, 3048, 3049, 3050, 3051, 3052, 3054, 3055, 3056, 3057, 3058, 3059, 3060, 3061, 3062, 3065, 3066, 3070, 3091, 3094, 3114, 3123, 3125, 3136, 3164, 3204, 3327, 3345, 3401, 3403, 3426, 3440, 3459, 3464, 3465, 3467, 3502, 3526, 3529, 3534, 3542, 3557, 3558, 3559, 3570, 3572, 3578, 3595, 3598, 3600, 3604, 3606, 3611, 3615, 3616, 3617, 3621, 3747, 3788, 3853, 3909, 3944, 3945, 3946, 3948, 3949, 3950, 3951, 3952, 3954, 3974, 3975, 3982, 3984, 3986, 3987, 3989, 3990, 3993, 3994, 3995, 3996, 4007, 4008, 4009, 4010, 4011, 4041, 4057, 4058, 4063, 4064, 4066, 4098, 4099, 4145, 4165, 4217, 4335, 4434, 4477, 4520, 4575, 4576, 4577, 4578, 4579, 4580, 4581, 4582, 4583, 4584, 4586, 4587, 4588, 4619, 4640, 4641, 4642, 4643, 4644, 4645, 4646, 4647, 4648, 4649, 4685, 4699, 4806, 4857, 5009, 5149, 5150, 5151, 5152, 5153, 5154, 5155, 5156, 5157, 5158, 5161, 5162, 5180, 5184, 5185, 5187, 5188, 5189, 5193, 5201, 5203, 5206, 5207, 5208, 5216, 5221, 5222, 5223, 5224, 5225, 5228, 5255, 5325, 5411, 5495, 5505, 5582, 5614, 5616, 5617, 5620, 5672, 5681, 5682, 5683, 5684, 5685, 5689, 5690, 5691, 5692, 5693, 5694, 5695, 5696, 5697, 5698, 5699, 5831, 5926, 6011, 6027, 6062, 6089, 6112, 6117, 6140, 6143, 6160, 6163, 6171, 6175, 6180, 6190, 6191, 6192, 6193, 6194, 6195, 6196, 6197, 6214, 6219, 6223, 6236, 6237, 6245, 6249, 6263, 6299, 6338, 6384, 6456, 6471, 6686, 6687, 6688, 6759, 6800, 6808, 6826, 6829, 6830, 6832, 6833, 6834, 6835, 6836, 6839, 6840, 6842, 6843, 6877, 6878, 6917, 6921, 6923, 6948, 7012, 7019, 7031, 7039, 7040, 7077, 7096, 7111, 7151, 7174, 7187, 7264, 7266, 7281, 7309, 7405, 7436, 7437, 7438, 7439, 7443, 7444, 7445, 7446, 7452, 7454, 7458, 7459, 7461, 7462, 7465, 7466, 7468, 7469, 7470, 7471, 7472, 7474, 7535, 7566, 7632, 7709, 7822, 7823, 7988, 8026, 8030, 8031, 8032, 8033, 8034, 8035, 8036, 8040, 8041, 8042, 8043, 8044, 8045, 8046, 8047, 8048, 8049, 8050, 8051, 8052, 8053, 8054, 8055, 8084, 8087, 8112, 8132, 8261, 8269, 8276, 8291, 8292, 8295, 8296, 8299, 8300, 8301, 8302, 8305, 8306, 8307, 8309, 8314, 8315, 8316, 8317, 8320, 8324, 8325, 8328, 8329, 8330, 8331, 8332, 8333, 8334, 8335, 8336, 8338, 8339, 8341, 8345, 8346, 8348, 8350, 8351, 8352, 8353, 8355, 8356, 8357, 8358, 8359, 8360, 8361, 8362, 8364, 8365, 8366, 8368, 8369, 8370, 8371, 8372, 8373, 8374, 8375, 8377, 8379, 8380, 8381, 8382, 8383, 8384, 8385, 8386, 8388, 8389, 8390, 8391, 8392, 8393, 8394, 8395, 8396, 8397, 8398, 8399, 8400, 8402, 8403, 8404, 8406, 8407, 8408, 8410, 8412, 8413, 8414, 8417, 8419, 8420, 8421, 8422, 8423, 8424, 8426, 8428, 8429, 8430, 8431, 8433, 8434, 8435, 8436, 8437, 8438, 8439, 8440, 8441, 8442, 8443, 8444, 8445, 8446, 8447, 8448, 8449, 8450, 8451, 8452, 8453, 8454, 8455, 8456, 8457, 8458, 8459, 8460, 8461, 8462, 8463, 8464, 8465, 8466, 8467, 8468, 8469, 8470, 8471, 8475, 8476, 8477, 8478, 8479, 8480, 8481, 8482, 8483, 8485, 8486, 8487, 8489, 8491, 8494, 8496, 8498, 8499, 8500, 8501, 8503, 8504, 8505, 8506, 8507, 8509, 8510, 8511, 8514, 8517, 8520, 8523, 8524, 8525, 8528, 8531, 8532, 8533, 8534, 8535, 8541, 8542, 8546, 8547, 8550, 8551, 8552, 8553, 8555, 8556, 8557, 8558, 8559, 8560, 8561, 8566, 8567, 8572, 8573, 8574, 8576, 8579, 8580, 8581, 8582, 8584, 8586, 8587, 8589, 8590, 8591, 8592, 8593, 8595, 8596, 8598, 8599, 8600, 8601, 8604, 8605, 8606, 8608, 8609, 8614, 8615, 8619, 8620, 8625, 8627, 8631, 8632, 8633, 8634, 8639, 8640, 8641, 8642, 8643, 8644, 8645, 8646, 8648, 8649, 8653, 8654, 8657, 8659, 8660, 8663, 8666, 8669, 8670, 8672, 8673, 8674, 8675, 8678, 8680, 8681, 8682, 8693, 8694, 8697, 8700, 8702, 8704, 8705, 8710, 8713, 8720, 8721, 8727, 8731, 8733, 8741, 8745, 8746, 8748, 8751, 8753, 8754, 8756, 8757, 8763, 8767, 8770, 8771, 8775, 8776, 8780, 8783, 8784, 8788, 8789, 8790, 8791, 8792, 8793, 8794, 8796, 8797, 8798, 8799, 8800, 8806, 8808, 8811, 8812, 8830, 8831, 8833, 8834, 8835, 8836, 8837, 8839, 8840, 8841, 8842, 8843, 8844, 8845, 8848, 8849, 8850, 8851, 8852, 8854, 8855, 8856, 8857, 8859, 8860, 8861, 8862, 8863, 8867, 8870, 8871, 8872, 8874, 8876, 8877, 8878, 8879, 8883, 8886, 8889, 8891, 8893, 8896, 8908, 8910, 8916, 8918, 8920, 8921, 8922, 8924, 8926, 8927, 8928, 8929, 8930, 8935, 8938, 8948, 8949, 8950, 8951, 8952, 8953, 8954, 8956, 8957, 8958, 8959, 8960, 8961, 8962, 8963, 8964, 8965, 8966, 8967, 8968, 8969, 8970, 8971, 8972, 8973, 8975, 8976, 8977, 8981, 8982, 8985, 8991, 8992, 8994, 8995, 8996, 8997, 8998, 8999, 9000, 9001, 9002, 9003, 9004, 9005, 9006, 9007, 9010, 9011, 9012, 9013, 9014, 9015, 9017, 9018, 9019, 9020, 9021, 9022, 9023, 9024, 9025, 9027, 9028, 9029, 9030, 9031, 9033, 9034, 9035, 9039, 9043, 9044, 9045, 9046, 9047, 9048, 9050, 9051, 9052, 9053, 9054, 9055, 9056, 9057, 9058, 9060, 9061, 9062, 9063, 9065, 9066, 9067, 9068, 9069, 9070, 9071, 9072, 9073, 9074, 9075, 9076, 9079, 9080, 9083, 9086, 9093, 9094, 9095, 9097, 9098, 9099, 9100, 9101, 9102, 9103, 9104, 9105, 9107, 9108, 9110, 9111, 9112, 9113, 9116, 9117, 9118, 9119, 9120, 9121, 9122, 9123, 9124, 9125, 9126, 9127, 9128, 9129, 9130, 9131, 9133, 9135, 9137, 9139, 9147, 9148, 9149, 9156, 9157, 9158, 9159, 9160, 9162, 9163, 9164, 9165, 9167, 9168, 9169, 9170, 9171, 9172, 9173, 9174, 9175, 9177, 9178, 9179, 9180, 9182, 9184, 9185, 9188, 9191, 9193, 9196, 9200, 9201, 9202, 9203, 9204, 9205, 9206, 9207, 9208, 9209, 9210, 9211, 9212, 9213, 9214, 9215, 9217, 9218, 9219, 9220, 9221, 9222, 9223, 9224, 9225, 9227, 9228, 9229, 9230, 9232, 9233, 9234, 9235, 9242, 9243, 9250, 9251, 9252, 9253, 9254, 9255, 9256, 9260, 9262, 9266, 9277, 9279, 9280, 9281, 9282, 9283, 9284, 9286, 9287, 9289, 9292, 9293, 9294, 9297, 9299, 9300, 9301, 9302, 9303, 9304, 9305, 9306, 9307, 9309, 9310, 9311, 9312, 9313, 9314, 9315, 9316, 9317, 9321, 9322, 9323, 9324, 9325, 9326, 9327, 9328, 9329, 9330, 9331, 9332, 9333, 9334, 9335, 9336, 9337, 9338, 9339, 9340, 9344, 9346, 9347, 9348, 9349, 9351, 9353, 9355, 9356, 9357, 9360, 9362, 9363, 9367, 9370, 9372, 9373, 9374, 9376, 9378, 9394, 9395, 9396, 9397, 9398, 9399, 9400, 9401, 9402, 9403, 9404, 9405, 9406, 9407, 9408, 9409, 9410, 9411, 9412, 9414, 9415, 9416, 9417, 9422, 9423, 9424, 9425, 9427, 9428, 9429, 9430, 9431, 9432, 9433, 9434, 9436, 9437, 9438, 9439, 9440, 9441, 9444, 9446, 9447, 9448, 9450, 9451, 9458, 9459, 9460, 9461, 9462, 9463, 9464, 9465, 9466, 9467, 9469, 9470, 9472, 9475, 9476, 9477, 9478, 9479, 9480, 9481, 9482, 9483, 9484, 9486, 9487, 9489, 9490, 9491, 9492, 9494, 9495, 9496, 9497, 9500, 9502, 9503, 9504, 9505, 9506, 9507, 9508, 9510, 9511, 9512, 9515, 9516, 9517, 9518, 9520, 9521, 9522, 9523, 9524, 9525, 9527, 9528, 9529, 9530, 9531, 9532, 9533, 9536, 9537, 9539, 9540, 9541, 9542, 9543, 9545, 9546, 9547, 9548, 9549, 9550, 9551, 9552, 9553, 9555, 9558, 9559, 9560, 9561, 9563, 9564, 9565, 9566, 9567, 9568, 9569, 9572, 9575, 9576, 9579, 9580, 9581, 9590, 9591, 9592, 9593, 9594, 9595, 9596, 9597, 9598, 9600, 9601, 9602, 9604, 9605, 9606, 9607, 9608, 9609, 9610, 9611, 9612, 9613, 9614, 9615, 9616, 9617, 9618, 9619, 9620, 9622, 9623, 9624, 9625, 9626, 9627, 9629, 9630, 9633, 9634, 9635, 9636, 9637, 9640, 9642, 9643, 9644, 9645, 9647, 9648, 9649, 9650, 9651, 9652, 9653, 9654, 9655, 9656, 9657, 9660, 9661, 9662, 9663, 9664, 9666, 9667, 9668, 9669, 9670, 9671, 9672, 9674, 9676, 9677, 9678, 9679, 9681, 9682, 9683, 9684, 9685, 9686, 9687, 9688, 9689, 9690, 9691, 9692, 9696, 9705, 9706, 9707, 9708, 9709, 9710, 9711, 9717, 9718, 9719, 9720, 9721, 9722, 9723, 9726, 9727, 9728, 9729, 9730, 9732, 9733, 9734, 9735, 9736, 9737, 9738, 9740, 9741, 9743, 9745, 9747, 9748, 9749, 9750, 9751, 9752, 9753, 9754, 9755, 9758, 9759, 9760, 9761, 9763, 9767, 9768, 9769, 9770, 9771, 9773, 9774, 9775, 9776, 9777, 9778, 9779, 9780, 9781, 9782, 9784, 9785, 9786, 9788, 9790, 9791, 9792, 9793, 9794, 9795, 9796, 9797, 9799, 9800, 9801, 9804, 9805, 9806, 9807, 9810, 9814, 9815, 9817, 9818, 9820, 9821, 9822, 9825, 9827, 9828, 9829, 9830, 9833, 9834, 9835, 9836, 9837, 9838, 9839, 9841, 9842, 9843, 9844, 9845, 9846, 9847, 9848, 9850, 9851, 9852, 9853, 9855, 9857, 9858, 9867, 9868, 9869, 9870, 9872, 9873, 9874, 9875, 9876, 9879, 9886, 9887, 9890, 9893, 9894, 9895, 9896, 9897, 9899, 9900, 9901, 9903, 9904, 9906, 9908, 9909, 9910, 9911, 9915, 9917, 9918, 9920, 9921, 9922, 9924, 9926, 9929, 9930, 9932, 9934, 9937, 9938, 9939, 9942, 9949, 9950, 9952, 9953, 9954, 9955, 9956, 9960, 9961, 9962, 9963, 9964, 9966, 9967, 9968, 9969, 9970, 9972, 9973, 9974, 9977, 9979, 9980, 9984, 9986, 9988, 9989, 9990, 9993, 9995, 10000, 10001, 10002, 10005, 10007, 10011, 10020, 10021, 10023, 10024, 10025, 10026, 10027, 10031, 10032, 10034, 10035, 10037, 10038, 10044, 10046, 10047, 10048, 10049, 10051, 10053, 10054, 10055, 10057, 10058, 10060, 10061, 10062, 10066, 10067, 10070, 10076, 10078, 10079, 10081, 10082, 10083, 10085, 10088, 10094, 10095, 10098, 10099, 10100, 10101, 10102, 10104, 10109, 10110, 10113, 10119, 10120, 10124, 10125, 10126, 10130, 10139, 10141, 10142, 10144, 10145, 10146, 10147, 10148, 10149, 10150, 10151, 10152, 10153, 10155, 10156, 10158, 10159, 10161, 10162, 10164, 10165, 10169, 10171, 10172, 10173, 10174, 10175, 10176, 10181, 10183, 10184, 10188, 10189, 10190, 10193, 10194, 10195, 10196, 10204, 10205, 10212, 10213, 10214, 10215, 10216, 10217, 10218, 10219, 10220, 10221, 10222, 10223, 10224, 10225, 10226, 10227, 10228, 10229, 10230, 10232, 10233, 10234, 10236, 10237, 10238, 10240, 10241, 10242, 10243, 10244, 10245, 10246, 10247, 10248, 10249, 10251, 10252, 10253, 10255, 10256, 10257, 10258, 10259, 10260, 10261, 10262, 10263, 10264, 10265, 10266, 10268, 10269, 10272, 10273, 10274, 10275, 10276, 10277, 10278, 10280, 10281, 10282, 10283, 10284, 10285, 10286, 10287, 10289
]

# =============================================================================
# SESSION
# =============================================================================

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Referer": "https://aniliberty.top/"
})

# =============================================================================
# ФУНКЦИИ
# =============================================================================

def log(msg: str):
    """Вывод с временной меткой."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def fetch_release(anime_id: int) -> Optional[Dict[str, Any]]:
    """Получает данные релиза с эпизодами и жанрами."""
    url = f"{RELEASE_URL}/{anime_id}"
    params = {"include": "genres,episodes"}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)

            if resp.status_code == 404:
                return None  # Релиз не найден

            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict) and "errors" in data:
                log(f"⚠️  ID {anime_id}: API ошибка — {data['errors']}")
                return None

            return data

        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
                continue
            raise
        except requests.exceptions.HTTPError:
            if resp.status_code >= 500 and attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
                continue
            raise
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
                continue
            raise

    return None


def download_poster(poster_url: str, folder: Path) -> Optional[str]:
    """Скачивает постер, пробуя jpg и webp."""
    if not poster_url:
        return None

    candidates = []
    if ".(jpg|webp)" in poster_url:
        for ext in ["jpg", "webp"]:
            candidates.append(poster_url.replace(".(jpg|webp)", f".{ext}"))
    else:
        candidates.append(poster_url)

    for url in candidates:
        try:
            r = session.get(url, timeout=TIMEOUT, stream=True)
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                ext = "webp" if "webp" in ct else "jpg"
                poster_path = folder / f"poster.{ext}"
                with open(poster_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                return str(poster_path)
        except Exception:
            continue

    return None


def extract_stream_links(episode: Dict[str, Any]) -> Dict[str, str]:
    """Извлекает прямые ссылки на видео из эпизода."""
    links = {}
    for quality in ["hls_1080", "hls_720", "hls_480"]:
        if episode.get(quality):
            links[quality] = episode[quality]
    if episode.get("rutube_id"):
        links["rutube"] = f"https://rutube.ru/video/{episode['rutube_id']}/"
    if episode.get("youtube_id"):
        links["youtube"] = f"https://www.youtube.com/watch?v={episode['youtube_id']}"
    return links


def build_metadata(data: Dict[str, Any], poster_path: Optional[str]) -> Dict[str, Any]:
    """Формирует объект metadata из ответа API."""
    poster = data.get("poster", {})
    optimized = poster.get("optimized", {}) if isinstance(poster, dict) else {}

    return {
        "id": data.get("id"),
        "name": data.get("name", {}),
        "alias": data.get("alias"),
        "year": data.get("year"),
        "season": data.get("season"),
        "description": data.get("description"),
        "age_rating": data.get("age_rating"),
        "episodes_total": data.get("episodes_total"),
        "is_ongoing": data.get("is_ongoing"),
        "is_in_production": data.get("is_in_production"),
        "publish_day": data.get("publish_day"),
        "genres": [g.get("name") for g in data.get("genres", []) if isinstance(g, dict)],
        "poster_url": optimized.get("thumbnail") or optimized.get("preview") or "",
        "_poster_path": poster_path or "",
        "_fetched_at": datetime.now().isoformat(),
    }


def build_episodes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Формирует список эпизодов со ссылками."""
    episodes = []
    for ep in data.get("episodes", []):
        if not isinstance(ep, dict):
            continue
        ep_copy = dict(ep)
        ep_copy["_stream_links"] = extract_stream_links(ep)
        episodes.append(ep_copy)
    return episodes


def make_local_m3u(metadata: Dict[str, Any], episodes: List[Dict[str, Any]], folder: Path) -> str:
    """Создает локальный M3U плейлист для аниме."""
    m3u_path = folder / "playlist.m3u"
    name = metadata.get("name", {}).get("main", "Unknown")

    lines = ["#EXTM3U"]
    for ep in sorted(episodes, key=lambda e: e.get("sort_order", 0) or e.get("ordinal", 0)):
        ep_name = ep.get("name") or f"Серия {ep.get('ordinal', '?')}"
        links = ep.get("_stream_links", {})

        stream_url = links.get("hls_1080") or links.get("hls_720") or links.get("hls_480")
        if stream_url:
            lines.append(f'#EXTINF:-1,{name} — {ep_name}')
            lines.append(stream_url)
        elif links.get("rutube"):
            lines.append(f'#EXTINF:-1,{name} — {ep_name} (Rutube)')
            lines.append(links["rutube"])
        elif links.get("youtube"):
            lines.append(f'#EXTINF:-1,{name} — {ep_name} (YouTube)')
            lines.append(links["youtube"])

    m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(m3u_path)


def make_main_m3u_entry(metadata: Dict[str, Any], episodes: List[Dict[str, Any]]) -> Optional[str]:
    """Создает блок M3U для общего плейлиста."""
    name = metadata.get("name", {}).get("main", "Unknown")
    year = metadata.get("year", "")
    poster = metadata.get("_poster_path", "").replace("\", "/")

    lines = []
    for ep in sorted(episodes, key=lambda e: e.get("sort_order", 0) or e.get("ordinal", 0)):
        ep_name = ep.get("name") or f"Серия {ep.get('ordinal', '?')}"
        links = ep.get("_stream_links", {})

        stream_url = links.get("hls_1080") or links.get("hls_720") or links.get("hls_480")
        if not stream_url:
            stream_url = links.get("rutube") or links.get("youtube")
            if stream_url:
                ep_name += " (External)"

        if not stream_url:
            continue

        group = f"{name}" + (f" ({year})" if year else "")
        lines.append(
            f'#EXTINF:-1 group-title="{group}" tvg-logo="{poster}",{name} — {ep_name}'
        )
        lines.append(stream_url)

    return "\n".join(lines) if lines else None


def process_anime(anime_id: int) -> tuple:
    """
    Полный цикл обработки одного аниме.
    Возвращает (status, result), где status: 'ok', 'skip', 'fail', 'not_found'
    """
    folder = MIRRORS_DIR / str(anime_id)
    meta_file = folder / "metadata.json"

    # Resume: если metadata.json уже есть и валиден — пропускаем
    if meta_file.exists() and meta_file.stat().st_size > 100:
        try:
            existing = json.loads(meta_file.read_text(encoding="utf-8"))
            if existing.get("id") == anime_id:
                return ("skip", anime_id)
        except Exception:
            pass

    folder.mkdir(parents=True, exist_ok=True)

    try:
        data = fetch_release(anime_id)
        if data is None:
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
            return ("not_found", anime_id)

        poster_url = ""
        if isinstance(data.get("poster"), dict):
            poster_url = data["poster"].get("optimized", {}).get("thumbnail", "")
        poster_path = download_poster(poster_url, folder)

        metadata = build_metadata(data, poster_path)
        meta_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        episodes = build_episodes(data)
        ep_file = folder / "episodes.json"
        ep_file.write_text(json.dumps(episodes, ensure_ascii=False, indent=2), encoding="utf-8")

        make_local_m3u(metadata, episodes, folder)
        m3u_block = make_main_m3u_entry(metadata, episodes)

        return ("ok", m3u_block)

    except Exception as e:
        return ("fail", (anime_id, str(e)))


def main():
    print("=" * 70)
    print("🎬  AniLiberty Mirror Scanner")
    print(f"   Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   ID для обработки: {len(ALL_IDS)}")
    print(f"   Потоков: {THREADS}")
    print("=" * 70)

    MIRRORS_DIR.mkdir(parents=True, exist_ok=True)

    stats = {"ok": 0, "skip": 0, "fail": 0, "not_found": 0, "no_streams": 0}
    m3u_blocks = []
    errors = {}

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(process_anime, aid): aid for aid in ALL_IDS}

        for i, future in enumerate(as_completed(futures), 1):
            status, result = future.result()

            if status == "ok":
                if result:
                    m3u_blocks.append(result)
                    stats["ok"] += 1
                else:
                    stats["no_streams"] += 1
            elif status == "skip":
                stats["skip"] += 1
            elif status == "not_found":
                stats["not_found"] += 1
            elif status == "fail":
                aid, err = result
                stats["fail"] += 1
                errors[str(aid)] = err
                log(f"❌ Ошибка ID {aid}: {err[:120]}")

            if i % 50 == 0 or i == len(ALL_IDS):
                log(
                    f"Прогресс: {i}/{len(ALL_IDS)} | "
                    f"OK:{stats['ok']} Skip:{stats['skip']} "
                    f"404:{stats['not_found']} Fail:{stats['fail']}"
                )

    if m3u_blocks:
        main_content = "#EXTM3U\n\n" + "\n\n".join(m3u_blocks) + "\n"
        MAIN_M3U.write_text(main_content, encoding="utf-8")

    if errors:
        ERRORS_FILE.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")

    LAST_SYNC_FILE.write_text(datetime.now().isoformat(), encoding="utf-8")

    print("\n" + "=" * 70)
    print("📊  СТАТИСТИКА")
    print(f"   ✅ Успешно:      {stats['ok']}")
    print(f"   ⏭️  Пропущено:    {stats['skip']}")
    print(f"   🔍 Не найдено:   {stats['not_found']}")
    print(f"   ⚠️  Без потоков:  {stats['no_streams']}")
    print(f"   ❌ Ошибок:       {stats['fail']}")
    print(f"   📁 Папок:        mirrors/anime/<id>/")
    print(f"   📄 Общий M3U:    {MAIN_M3U}")
    if errors:
        print(f"   📝 Ошибки:       {ERRORS_FILE}")
    print("=" * 70)

    return 0 if stats["fail"] < len(ALL_IDS) * 0.2 else 1


if __name__ == "__main__":
    sys.exit(main())
