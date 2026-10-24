# Servidor autoritativo de Pong (TCP)
# Es la ÚNICA fuente de verdad: calcula la física y envía el estado
# a los dos clientes TICKS veces por segundo. No usa pygame.
# Configuración y mensajes: protocolo.py
import math
import random
import socket
import threading
import time

import protocolo as p


# =====================================================================
# Juego: estado y física. No sabe nada de red.
# =====================================================================
class Juego:
    def __init__(self):
        self.pelota = [p.ANCHO / 2, p.ALTO / 2]        # centro (x, y)
        self.vel = [0.0, 0.0]                          # velocidad (vx, vy) en px/s
        self.color = "white"
        self.paletas = {rol: p.ALTO / 2 for rol in p.ROLES}     # rol -> y del centro
        self.inputs = {rol: p.Input() for rol in p.ROLES}       # último input de cada jugador
        self.puntos = [0, 0]                           # [izq, der]
        self.reiniciar_pelota(random.choice([-1, 1]))

    # ---------- Auxiliares ----------
    def reiniciar_pelota(self, direccion):
        """Pelota al centro con saque aleatorio. direccion = 1 (derecha) o -1 (izquierda)."""
        angulo = math.radians(random.uniform(-p.ANGULO_SAQUE, p.ANGULO_SAQUE))
        self.pelota = [p.ANCHO / 2, p.ALTO / 2]
        self.vel = [direccion * p.VEL_INICIAL * math.cos(angulo),
                    p.VEL_INICIAL * math.sin(angulo)]

    def _cambiar_color(self):
        """Color aleatorio distinto del actual."""
        self.color = random.choice([c for c in p.COLORES if c != self.color])

    @staticmethod
    def _choca(bx, by, px, py):
        """¿El cuadrado que envuelve la pelota (bx, by) toca la paleta con centro (px, py)?"""
        return (abs(bx - px) <= p.RADIO_PELOTA + p.ANCHO_PALETA / 2 and
                abs(by - py) <= p.RADIO_PELOTA + p.ALTO_PALETA / 2)

    @staticmethod
    def _rebote_paleta(vx, vy, y_pelota, y_paleta, direccion):
        """Nueva velocidad tras golpear una paleta: el ángulo depende de dónde golpea."""
        relativo = (y_pelota - y_paleta) / (p.ALTO_PALETA / 2)   # -1 arriba, 0 centro, 1 abajo
        relativo = max(-1, min(1, relativo))
        rapidez = min(math.hypot(vx, vy) * p.ACELERACION, p.VEL_MAXIMA)
        angulo = math.radians(relativo * p.ANGULO_MAX)
        return [direccion * rapidez * math.cos(angulo), rapidez * math.sin(angulo)]

    # ---------- Simulación ----------
    def actualizar(self, dt):
        """Avanza la simulación dt segundos."""
        # 1) Paletas según el input de cada jugador, limitadas a la pantalla
        medio = p.ALTO_PALETA / 2
        for rol in p.ROLES:
            y = self.paletas[rol]
            if self.inputs[rol].arriba:
                y -= p.VEL_PALETA * dt
            if self.inputs[rol].abajo:
                y += p.VEL_PALETA * dt
            self.paletas[rol] = max(medio, min(p.ALTO - medio, y))

        # 2) Movimiento de la pelota: posición += velocidad * dt
        x, y = self.pelota
        vx, vy = self.vel
        x += vx * dt
        y += vy * dt

        # 3) Techo y piso (solo si va hacia la pared, para no quedarse pegada)
        if y - p.RADIO_PELOTA <= 0 and vy < 0:
            y, vy = p.RADIO_PELOTA, -vy
            self._cambiar_color()
        elif y + p.RADIO_PELOTA >= p.ALTO and vy > 0:
            y, vy = p.ALTO - p.RADIO_PELOTA, -vy
            self._cambiar_color()

        # 4) Paletas (solo si la pelota se mueve hacia esa paleta)
        x_izq, x_der = p.X_PALETA["izq"], p.X_PALETA["der"]
        if vx > 0 and self._choca(x, y, x_der, self.paletas["der"]):
            x = x_der - p.ANCHO_PALETA / 2 - p.RADIO_PELOTA      # se saca de la paleta
            vx, vy = self._rebote_paleta(vx, vy, y, self.paletas["der"], -1)
            self._cambiar_color()
        elif vx < 0 and self._choca(x, y, x_izq, self.paletas["izq"]):
            x = x_izq + p.ANCHO_PALETA / 2 + p.RADIO_PELOTA
            vx, vy = self._rebote_paleta(vx, vy, y, self.paletas["izq"], 1)
            self._cambiar_color()

        self.pelota = [x, y]
        self.vel = [vx, vy]

        # 5) Puntos: la pelota salió por un lado; saca hacia quien perdió el punto
        if x + p.RADIO_PELOTA < 0:
            self.puntos[1] += 1
            self.reiniciar_pelota(-1)
        elif x - p.RADIO_PELOTA > p.ANCHO:
            self.puntos[0] += 1
            self.reiniciar_pelota(1)

    def a_mensaje(self, jugando):
        """Empaqueta el estado actual en un mensaje Estado (redondeado para ocupar menos)."""
        return p.Estado(
            jugando=jugando,
            pelota=[round(self.pelota[0], 1), round(self.pelota[1], 1)],
            color=self.color,
            paletas={rol: round(y, 1) for rol, y in self.paletas.items()},
            puntos=list(self.puntos),
        )


# =====================================================================
# ServidorPong: conexiones, hilos y bucle principal
# =====================================================================
class ServidorPong:
    def __init__(self, host="0.0.0.0", puerto=p.PUERTO):
        self.host, self.puerto = host, puerto
        self.juego = Juego()
        self.clientes = {}              # rol -> Conexion
        # Protege self.juego y self.clientes: los usan el hilo que acepta,
        # un hilo por cliente y el bucle del juego al mismo tiempo
        self.lock = threading.Lock()

    def iniciar(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Permite reiniciar el servidor sin esperar a que el SO libere el puerto
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.puerto))
        sock.listen()
        print(f"Servidor de Pong v{p.VERSION} en {ip_local()}:{self.puerto}  (Ctrl+C para detener)")

        threading.Thread(target=self._aceptar, args=(sock,), daemon=True).start()
        try:
            self._bucle()
        except KeyboardInterrupt:
            print("\nServidor detenido")
        finally:
            sock.close()

    # ---------- Conexiones ----------
    def _aceptar(self, sock_servidor):
        """Hilo que acepta clientes y les asigna un lado libre."""
        while True:
            sock, direccion = sock_servidor.accept()
            conexion = p.Conexion(sock)

            with self.lock:
                rol = next((r for r in p.ROLES if r not in self.clientes), None)
                if rol is not None:
                    try:
                        # La bienvenida va antes de registrarlo: así el primer
                        # mensaje que recibe el cliente siempre es su rol
                        conexion.enviar(p.Bienvenida(rol=rol))
                        self.clientes[rol] = conexion
                    except p.ConexionCerrada:
                        conexion.cerrar()
                        continue

            if rol is None:
                try:
                    conexion.enviar(p.Lleno())
                except p.ConexionCerrada:
                    pass
                conexion.cerrar()
                print(f"[x] Rechazado {direccion}: partida llena")
                continue

            print(f"[+] {direccion} conectado como '{rol}'")
            threading.Thread(target=self._atender, args=(rol, conexion), daemon=True).start()

    def _atender(self, rol, conexion):
        """Hilo por cliente: recibe sus mensajes y actualiza su input."""
        try:
            while True:
                msg = conexion.recibir()
                if isinstance(msg, p.Input):
                    with self.lock:
                        self.juego.inputs[rol] = msg
        except p.ConexionCerrada:
            pass
        finally:
            self._desconectar(rol, conexion)

    def _desconectar(self, rol, conexion):
        """Saca a un cliente de la partida. Se puede llamar dos veces sin problema."""
        with self.lock:
            # Solo se borra si el rol sigue siendo de ESTA conexión
            # (podría haberse reconectado otro cliente en el mismo lado)
            if self.clientes.get(rol) is conexion:
                del self.clientes[rol]
                self.juego.inputs[rol] = p.Input()
                self.juego.reiniciar_pelota(random.choice([-1, 1]))   # partida en pausa
                print(f"[-] Jugador '{rol}' desconectado. Esperando reconexión...")
        conexion.cerrar()

    # ---------- Bucle principal ----------
    def _bucle(self):
        """Física a TICKS por segundo y envío del estado a todos los clientes."""
        periodo = 1 / p.TICKS
        anterior = time.perf_counter()
        while True:
            inicio = time.perf_counter()
            dt = min(inicio - anterior, p.DT_MAXIMO)
            anterior = inicio

            with self.lock:
                jugando = len(self.clientes) == len(p.ROLES)   # sin rival, en pausa
                if jugando:
                    self.juego.actualizar(dt)
                estado = self.juego.a_mensaje(jugando)
                destinos = list(self.clientes.items())

            # El envío va FUERA del lock: un cliente lento no frena
            # a los hilos que reciben inputs
            for rol, conexion in destinos:
                try:
                    conexion.enviar(estado)
                except p.ConexionCerrada:
                    self._desconectar(rol, conexion)

            # Espera lo que falta para completar el periodo
            espera = periodo - (time.perf_counter() - inicio)
            if espera > 0:
                time.sleep(espera)


def ip_local():
    """IP de esta máquina en la red local (la que deben usar los clientes)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # UDP: no envía nada, solo elige la interfaz de salida
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    ServidorPong().iniciar()
