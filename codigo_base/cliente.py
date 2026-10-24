# Cliente de Pong: envía las teclas al servidor y dibuja el estado que recibe.
# No calcula física: todo lo que muestra viene del servidor.
# Configuración y mensajes: protocolo.py
# Uso:  python cliente.py <IP_del_servidor>     (sin IP usa 127.0.0.1)
import sys
import threading

import pygame

import protocolo as p


# =====================================================================
# ClienteRed: conexión con el servidor + hilo receptor
# =====================================================================
class ClienteRed:
    def __init__(self, ip):
        self.conexion = p.Conexion.conectar(ip)
        # El hilo receptor escribe estos atributos y el bucle de pygame los lee
        self.lock = threading.Lock()
        self.estado = None          # último p.Estado recibido
        self.rol = None             # "izq" o "der", lo asigna el servidor
        self.conectado = True
        self.error = ""
        self._ultimo_input = None
        threading.Thread(target=self._recibir, daemon=True).start()

    def _recibir(self):
        """Hilo receptor: procesa cada mensaje según su clase."""
        try:
            while True:
                msg = self.conexion.recibir()
                with self.lock:
                    if isinstance(msg, p.Estado):
                        self.estado = msg
                    elif isinstance(msg, p.Bienvenida):
                        if msg.version != p.VERSION:
                            self.error = (f"Versión distinta: servidor v{msg.version}, "
                                          f"cliente v{p.VERSION}. Actualiza protocolo.py")
                            break
                        self.rol = msg.rol
                    elif isinstance(msg, p.Lleno):
                        self.error = "La partida está llena"
        except p.ConexionCerrada:
            pass
        with self.lock:
            self.conectado = False
            if not self.error:
                self.error = "Conexión perdida con el servidor"
        self.conexion.cerrar()

    def enviar_input(self, arriba, abajo):
        """Envía las teclas solo cuando cambian (menos tráfico; TCP garantiza la entrega)."""
        entrada = p.Input(arriba=arriba, abajo=abajo)
        if entrada == self._ultimo_input:      # __eq__ lo genera @dataclass
            return
        try:
            self.conexion.enviar(entrada)
            self._ultimo_input = entrada
        except p.ConexionCerrada:
            pass                               # el hilo receptor detecta la caída

    def foto(self):
        """Copia consistente del estado (se toma el lock una sola vez por frame)."""
        with self.lock:
            return self.estado, self.rol, self.conectado, self.error

    def cerrar(self):
        self.conexion.cerrar()


# =====================================================================
# Pantalla: todo el dibujo con pygame
# =====================================================================
class Pantalla:
    def __init__(self, titulo):
        pygame.init()
        self.screen = pygame.display.set_mode((p.ANCHO, p.ALTO))
        pygame.display.set_caption(titulo)
        self.fuente_titulo = pygame.font.Font(None, 72)
        self.fuente_grande = pygame.font.Font(None, 96)
        self.fuente_chica = pygame.font.Font(None, 40)

    def _texto(self, fuente, texto, color, centro):
        """Dibuja un texto centrado en el punto 'centro'."""
        sup = fuente.render(texto, True, color)
        self.screen.blit(sup, sup.get_rect(center=centro))

    def dibujar(self, est, rol, conectado, error):
        self.screen.fill("black")

        if est is not None:
            # Línea central punteada
            for y in range(0, p.ALTO, 40):
                pygame.draw.rect(self.screen, "gray30", (p.ANCHO // 2 - 2, y + 10, 4, 20))
            # Marcador
            self._texto(self.fuente_grande, str(est.puntos[0]), "white", (p.ANCHO / 4, 70))
            self._texto(self.fuente_grande, str(est.puntos[1]), "white", (3 * p.ANCHO / 4, 70))
            # Paletas: el servidor envía la y del centro de cada una
            for rol_paleta in p.ROLES:
                r = pygame.Rect(0, 0, p.ANCHO_PALETA, p.ALTO_PALETA)
                r.center = (round(p.X_PALETA[rol_paleta]), round(est.paletas[rol_paleta]))
                pygame.draw.rect(self.screen, "white", r)

        # Indicador del lado que controla este cliente
        if rol is not None:
            x_tu = p.ANCHO / 4 if rol == "izq" else 3 * p.ANCHO / 4
            self._texto(self.fuente_chica, "TÚ", "gray60", (x_tu, 130))

        # Título PONG abajo y centrado
        titulo = self.fuente_titulo.render("PONG", True, "white")
        self.screen.blit(titulo, titulo.get_rect(midbottom=(p.ANCHO / 2, p.ALTO - 15)))

        # Pelota al final para que quede encima de todo
        if est is not None:
            pygame.draw.circle(self.screen, est.color, est.pelota, p.RADIO_PELOTA)

        # Avisos de conexión
        if not conectado:
            aviso = error
        elif est is None:
            aviso = "Conectando..."
        elif not est.jugando:
            aviso = "Esperando rival..."
        else:
            aviso = None
        if aviso:
            self._texto(self.fuente_chica, aviso, "yellow", (p.ANCHO / 2, p.ALTO / 2 - 100))

        pygame.display.flip()


# =====================================================================
# Programa principal
# =====================================================================
def main():
    ip = "192.168.1.4"
    try:
        red = ClienteRed(ip)
    except OSError as e:
        print(f"No se pudo conectar a {ip}:{p.PUERTO} -> {e}")
        sys.exit(1)

    pantalla = Pantalla(f"PONG - cliente ({ip})")
    clock = pygame.time.Clock()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        # Cada cliente controla SU paleta: sirven W/S o las flechas
        keys = pygame.key.get_pressed()
        red.enviar_input(arriba=bool(keys[pygame.K_w] or keys[pygame.K_UP]),
                         abajo=bool(keys[pygame.K_s] or keys[pygame.K_DOWN]))

        pantalla.dibujar(*red.foto())
        clock.tick(60)

    red.cerrar()
    pygame.quit()


if __name__ == "__main__":
    main()
