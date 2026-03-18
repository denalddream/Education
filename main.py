import socket
import json
import time

# Настройки сети UDP
UDP_IP = "127.0.0.1"
UDP_PORT_RX = 5005  # Порт для получения данных от системы
UDP_PORT_TX = 5006  # Порт для отправки управляющих сигналов


class AKVController:
    def __init__(self):
        self.state = "INIT"
        self.ready = False
        self.error = False
        self.last_msg_time = time.time()
        self.state_timer = 0
        self.bosv_timer = 0
        self.bosv_col_a = True

        # Кэш для хранения последних полученных данных
        self.last_telemetry = {}

        # Выходные сигналы и состояния
        self.out = {
            "state": self.state,
            "ready": False,
            "ek1": False, "ek2_ek7": False,
            "ya1": False, "ya2": False, "ya3": False,
            "ya4_ya5": False, "ya6_ya7": False,
            "motor_380v": False, "error_msg": "",
            "filter_warning": False, "ready_to_inject": False,
            "comm_loss": False
        }

    def process_telemetry(self, data):
        now = time.time()

        # Обновляем таймер связи и кэш только при наличии реальных данных
        if data:
            self.last_msg_time = now
            self.last_telemetry = data
        else:
            # Если данных нет (например, при BlockingIOError), берем последние известные
            data = self.last_telemetry

        # Извлечение данных с датчиков с фолбэками по умолчанию
        p110 = data.get("power_110v", False)
        p380 = data.get("power_380v", False)
        fan = data.get("fan_power", False)
        sp1 = data.get("sp1_closed", True)
        t_mix = data.get("t_mix", 50)
        t_oil = data.get("t_oil", 20)
        t_stator = data.get("t_stator", 50)
        t_env = data.get("t_env", 20)
        fu1 = data.get("fu1_open", False)
        cmd_start = data.get("cmd_start", False)
        cmd_inject = data.get("cmd_inject", False)
        cmd_reset = data.get("cmd_reset", False)  # Команда сброса ошибок
        pressure = data.get("pressure", 1.0)
        filter_dp = data.get("filter_dp_kpa", 0)

        # Блок 1: Инициализация и формирование сигнала «Готовность»
        if self.state == "INIT":
            self.state_timer = 0  # Сброс таймеров для чистого запуска
            self.bosv_timer = 0

            if p110 and p380:
                if sp1 and t_mix < 110 and -25 <= t_oil <= 100 and t_stator < 150:
                    self.ready = True
                    self.state = "WAIT_START"
                elif t_oil < -25 and t_env < -25:
                    self.state = "HEATING"
                else:
                    self.ready = False
            else:
                self.ready = False

        # Блок 2: Алгоритм подогрева масла
        elif self.state == "HEATING":
            self.out["ek1"] = True
            if fu1:
                self.error = True
                self.out["error_msg"] = "Перегрев подогревателя ЕК1 (FU1 разомкнут)"
                self.out["ek1"] = False
                self.state = "ERROR"
            elif t_oil >= -25:
                self.out["ek1"] = False
                self.ready = True
                self.state = "WAIT_START"

        # Блок 3: Пуск агрегата (режим холостого хода)
        elif self.state == "WAIT_START":
            if self.ready and cmd_start:
                self.out["ya1"] = False
                self.out["motor_380v"] = True
                self.state_timer = now
                self.state = "START_CHECK_ROTATION"

        elif self.state == "START_CHECK_ROTATION":
            if now - self.state_timer <= 2.0:
                if not sp1:  # SP1 разомкнут = давление 0.1 МПа
                    self.ready = False
                    self.out["motor_380v"] = False
                    self.error = True
                    self.out["error_msg"] = "Неправильное направление вращения"
                    self.state = "ERROR"
            else:
                self.state = "WARMUP"

        # Блок 4: Прогрев и переход в рабочий режим
        elif self.state == "WARMUP":
            if t_oil > 3:
                self.out["ready_to_inject"] = True
                self.state_timer = now
                self.state = "WARMUP_IDLE"

        elif self.state == "WARMUP_IDLE":
            if now - self.state_timer >= 5.0:
                self.state = "WORKING"
                self.bosv_timer = now

        # Блок 5: Рабочий цикл и алгоритм управления клапанами (БОСВ)
        elif self.state == "WORKING":
            # Условие 6.1: Команда на останов
            if not cmd_inject or not fan:
                self.state = "STOPPING"
                self.state_timer = now
                self.out["ya1"] = False
                # Немедленный сброс конденсата БОСВ
                self.out["ya2"] = True
                self.out["ya3"] = True
            else:
                # Защита от превышения давления
                if pressure >= 1.25:
                    self.out["ya1"] = False
                    self.out["ya2"] = False
                    self.out["ya3"] = False
                elif pressure <= 1.15:
                    self.out["ya1"] = True

                # Цикл работы БОСВ (каждые 90 секунд)
                bosv_elapsed = now - self.bosv_timer
                if bosv_elapsed < 4.0:
                    self.out["ya2"] = True
                    self.out["ya3"] = True
                else:
                    self.out["ya2"] = False
                    self.out["ya3"] = False

                if bosv_elapsed >= 90.0:
                    self.bosv_col_a = not self.bosv_col_a
                    self.bosv_timer = now

                if self.bosv_col_a:
                    self.out["ya4_ya5"] = True
                    self.out["ya6_ya7"] = False
                else:
                    self.out["ya4_ya5"] = False
                    self.out["ya6_ya7"] = True

                # Обогрев клапанов
                if t_env < 3:
                    self.out["ek2_ek7"] = True
                elif t_env >= 5:
                    self.out["ek2_ek7"] = False

        # Блок 6: Останов АКВ
        elif self.state == "STOPPING":
            # Таймер задержки не менее 60 секунд
            if now - self.state_timer >= 60.0:
                self.out["motor_380v"] = False
                self.out["ya2"] = False
                self.out["ya3"] = False
                self.state_timer = 0  # Обнуляем таймеры перед уходом в INIT
                self.bosv_timer = 0
                self.state = "INIT"

                # Блок обработки ошибок
        elif self.state == "ERROR":
            self.out["ready"] = False
            self.out["motor_380v"] = False
            self.out["ek1"] = False
            self.out["ya1"] = False
            self.out["ya2"] = False
            self.out["ya3"] = False

            # Ожидание команды на сброс ошибки от оператора
            if cmd_reset:
                self.error = False
                self.out["error_msg"] = ""
                self.state = "INIT"

        # Параллельные защитные и информационные процессы
        self.out["filter_warning"] = (filter_dp >= 8.0)
        self.out["comm_loss"] = (now - self.last_msg_time > 5.0)
        self.out["state"] = self.state
        self.out["ready"] = self.ready


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT_RX))
    sock.setblocking(False)

    controller = AKVController()

    print(f"UDP сервер запущен. Прием на порту {UDP_PORT_RX}, отправка на порт {UDP_PORT_TX}")

    while True:
        try:
            # Получение данных (до 1024 байт)
            data_bytes, addr = sock.recvfrom(1024)
            telemetry = json.loads(data_bytes.decode('utf-8'))

            # Обновление состояния контроллера с новыми данными
            controller.process_telemetry(telemetry)

        except BlockingIOError:
            # Если новых данных нет, обновляем таймеры со старыми данными
            controller.process_telemetry({})
            time.sleep(0.1)  # Небольшая пауза, чтобы не грузить процессор
            continue  # Пропускаем отправку ответа, если нет входящего пакета (опционально)

        except json.JSONDecodeError:
            print("Ошибка декодирования JSON")
            continue
        except Exception as e:
            print(f"Непредвиденная ошибка: {e}")
            time.sleep(1)
            continue

        # Отправка управляющих сигналов (выполняется, если был получен пакет)
        try:
            response_data = json.dumps(controller.out).encode('utf-8')
            sock.sendto(response_data, (UDP_IP, UDP_PORT_TX))
        except Exception as e:
            print(f"Ошибка отправки данных: {e}")


if __name__ == "__main__":
    main()