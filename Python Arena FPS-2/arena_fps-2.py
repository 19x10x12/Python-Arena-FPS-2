"""
=====================================================================
 ARENA SANDBOX FPS  -  3D first-person shooter met de Ursina engine
=====================================================================
 Installeren :  pip install ursina
 Starten     :  python arena_fps.py

 Alles zit in dit ene bestand: geen externe afbeeldingen of modellen
 nodig (alleen ingebouwde Ursina-vormen en -texturen).
 De besturing staat onderaan dit bestand.
"""

from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
import math
import random

# ---------------------------------------------------------------------
#  Instellingen
# ---------------------------------------------------------------------
ARENA = 30                       # halve breedte van de arena (60 x 60)
WALL_H = 8                       # hoogte van de buitenmuren
PLAYER_START = Vec3(0, 1, -22)
PLAYER_MAX_HP = 100.0
AGGRO_RANGE = 30                 # NPC's draaien naar je toe binnen deze afstand
SHOOT_RANGE = 32                 # NPC's schieten binnen deze afstand


def C(hex_code):
    """Korte helper voor hex-kleuren (zonder alpha)."""
    return color.hex(hex_code[:7])


_ColorClass = type(color.white)


def rgba(r, g, b, a):
    """Kleur met transparantie (waardes 0-1), werkt op alle Ursina-versies."""
    return _ColorClass(r, g, b, a)


# ---------------------------------------------------------------------
#  Obstakel-administratie (voor NPC-botsing en kogels)
# ---------------------------------------------------------------------
class Block:
    """Rechthoekig obstakel: de Entity zelf heeft een echte collider (voor
    de speler en hitscan); dit object wordt gebruikt voor NPC's en projectielen."""

    def __init__(self, entity, width, depth, height, dynamic=False):
        self.entity = entity
        self.x = entity.x
        self.z = entity.z
        self.hw = width / 2
        self.hd = depth / 2
        self.h = height
        self.dynamic = dynamic


# ---------------------------------------------------------------------
#  Projectielen
# ---------------------------------------------------------------------
class Projectile(Entity):
    """Basisklasse voor alles wat door de lucht vliegt (kogels, raketten)."""

    def __init__(self, game, position, direction, speed, life, **kwargs):
        super().__init__(position=position, **kwargs)
        self.game = game
        self.direction = direction.normalized()
        self.speed = speed
        self.life = life
        self.alive = True
        game.projectiles.append(self)

    def update(self):
        if not self.alive or self.game.halted:
            return
        dt = min(time.dt, 0.05)
        self.life -= dt
        dist = self.speed * dt
        steps = max(1, int(dist / 0.35) + 1)       # voorkomt door muren heen vliegen
        for _ in range(steps):
            self.position += self.direction * (dist / steps)
            if self.check_hit():
                return
        if self.life <= 0:
            self.expire()

    def check_hit(self):
        return False

    def expire(self):
        self.remove()

    def remove(self):
        if not self.alive:
            return
        self.alive = False
        if self in self.game.projectiles:
            self.game.projectiles.remove(self)
        destroy(self)


class EnemyBullet(Projectile):
    """Zichtbaar projectiel van een vijand."""

    def __init__(self, game, position, direction, damage=8, speed=24):
        super().__init__(game, position, direction, speed, 4,
                         model='sphere', color=C('#ff5a1f'), scale=.22)
        self.damage = damage

    def check_hit(self):
        g = self.game
        if g.point_blocked(self.position):
            self.remove()
            return True
        center = g.player.position + Vec3(0, 1, 0)
        if (self.position - center).length() < 0.9:
            g.damage_player(self.damage)
            self.remove()
            return True
        return False


class Explosion(Entity):
    """Groeiende vuurbal die na een fractie van een seconde verdwijnt."""

    def __init__(self, position, radius):
        super().__init__(model='sphere', color=C('#ff8a1f'), position=position, scale=.4)
        Entity(parent=self, model='sphere', color=C('#ffe066'), scale=.6)
        r = radius * 1.8
        self.animate_scale(Vec3(r, r, r), duration=.25, curve=curve.out_expo)
        destroy(self, delay=.32)


class Rocket(Projectile):
    """Raket van de Rocket Launcher: ontploft met splash damage."""

    def __init__(self, game, position, direction, damage, radius):
        super().__init__(game, position, direction, 42, 5,
                         model='cube', color=C('#ff7a00'), scale=(.14, .14, .5))
        self.look_at(self.position + self.direction)
        self.damage = damage
        self.radius = radius
        self.trail_t = 0

    def update(self):
        if not self.alive or self.game.halted:
            return
        self.trail_t -= time.dt
        if self.trail_t <= 0:
            self.trail_t = .03
            puff = Entity(model='sphere', color=C('#bbbbbb'), position=self.position, scale=.18)
            puff.animate_scale(Vec3(.02, .02, .02), duration=.4)
            destroy(puff, delay=.42)
        super().update()

    def check_hit(self):
        g = self.game
        hit = g.point_blocked(self.position)
        if not hit:
            for e in g.enemies:
                if (e.position + Vec3(0, 1, 0) - self.position).length() < 1.1:
                    hit = True
                    break
        if hit:
            self.explode()
            return True
        return False

    def expire(self):
        self.explode()

    def explode(self):
        if not self.alive:
            return
        pos = Vec3(self.x, max(self.y, .3), self.z)
        game, dmg, rad = self.game, self.damage, self.radius
        self.remove()
        game.explode(pos, rad, dmg)


# ---------------------------------------------------------------------
#  Vijandelijke NPC
# ---------------------------------------------------------------------
class Enemy(Entity):
    def __init__(self, game, position):
        super().__init__(position=position)
        self.game = game
        self.max_hp = 100.0
        self.hp = self.max_hp
        self.dead = False
        self.speed = random.uniform(2.2, 3.2)
        self.strafe = random.choice((-1, 1))
        self.strafe_timer = random.uniform(1.5, 4)
        self.wander_dir = None
        self.wander_timer = 0
        self.fire_timer = random.uniform(1.0, 2.5)
        self.flash_timer = 0
        self.gun_flash_timer = 0
        self.walk_t = random.uniform(0, 6)
        self.rotation_y = random.uniform(0, 360)
        self.parts = []                     # (entity, originele kleur) voor rood flitsen

        shirt = random.choice(['#2f5fa8', '#3d7a3d', '#6b4f9e', '#7a7a7a'])
        skin = random.choice(['#e0ac69', '#c68642', '#8d5524', '#f1c27d'])

        # lichaam + hoofd (met colliders, zodat je ze kunt raken)
        self.body = self._part('cube', shirt, (0, 1.1, 0), (.8, 1.0, .4), collider='box')
        self.head = self._part('sphere', skin, (0, 1.9, 0), (.5, .5, .5), collider='box')
        self.body.enemy = self
        self.head.enemy = self
        # helm, benen, armen
        Entity(parent=self, model='cube', color=C('#2a2f2a'), position=(0, 2.1, 0), scale=(.56, .14, .56))
        self.leg_l = self._part('cube', '#2b2b45', (-.2, .65, 0), (.3, .65, .32), origin_y=.5)
        self.leg_r = self._part('cube', '#2b2b45', (.2, .65, 0), (.3, .65, .32), origin_y=.5)
        self.arm_l = self._part('cube', shirt, (-.52, 1.55, 0), (.2, .7, .22), origin_y=.5)
        self._part('cube', shirt, (.52, 1.3, .2), (.2, .2, .55))          # rechterarm naar voren
        # wapen in de hand
        Entity(parent=self, model='cube', color=C('#1c1c1f'), position=(.52, 1.3, .65), scale=(.1, .14, .7))
        self.muzzle = Entity(parent=self, position=(.52, 1.3, 1.05))
        self.gun_flash = Entity(parent=self.muzzle, model='sphere', color=C('#ffd34d'),
                                scale=.28, enabled=False)

        # healthbar boven het hoofd
        self.hp_bar = Entity(parent=self, y=2.6)
        Entity(parent=self.hp_bar, model='cube', color=C('#2b0a0a'), scale=(1.0, .13, .03))
        self.hp_fg = Entity(parent=self.hp_bar, model='cube', color=C('#2ecc40'),
                            scale=(1, .09, .06), origin_x=-.5, x=-.5)

    def _part(self, model, col, pos, scl, collider=None, **kw):
        if collider:
            kw['collider'] = collider
        e = Entity(parent=self, model=model, color=C(col), position=pos, scale=scl, **kw)
        self.parts.append((e, e.color))
        return e

    # ----- schade & dood -----
    def set_flash(self, on):
        for ent, orig in self.parts:
            ent.color = C('#ff1a1a') if on else orig

    def update_bar(self):
        ratio = max(0.0, self.hp / self.max_hp)
        self.hp_fg.scale_x = max(0.001, ratio)
        if ratio > .5:
            self.hp_fg.color = C('#2ecc40')
        elif ratio > .25:
            self.hp_fg.color = C('#f1c40f')
        else:
            self.hp_fg.color = C('#e74c3c')

    def take_damage(self, amount):
        if self.dead:
            return
        self.hp -= amount
        self.flash_timer = .12
        self.set_flash(True)
        self.update_bar()
        if self.hp <= 0:
            self.die()

    def die(self):
        self.dead = True
        g = self.game
        if self in g.enemies:
            g.enemies.remove(self)
        g.kills += 1
        self.hp_bar.enabled = False
        self.gun_flash.enabled = False
        self.set_flash(False)
        try:                                    # lijk blokkeert geen kogels meer
            self.body.collider = None
            self.head.collider = None
        except Exception:
            pass
        self.animate('rotation_x', -90, duration=.5, curve=curve.in_quad)
        destroy(self, delay=3)

    # ----- bewegen -----
    def try_move(self, dx, dz, speed, dt):
        g = self.game
        limit = ARENA - 1.5
        nx = max(-limit, min(limit, self.x + dx * speed * dt))
        nz = max(-limit, min(limit, self.z + dz * speed * dt))
        px, pz = g.player.x, g.player.z
        d_new = math.hypot(nx - px, nz - pz)
        d_old = math.hypot(self.x - px, self.z - pz)
        if d_new < 1.6 and d_new < d_old:       # niet door de speler lopen
            return False
        stuck_inside = g.blocked(self.x, self.z, .5)    # ontsnappen als er iets op ze gespawnd is
        moved = False
        if stuck_inside or not g.blocked(nx, self.z, .5):
            if abs(nx - self.x) > 1e-5:
                self.x = nx
                moved = True
        if stuck_inside or not g.blocked(self.x, nz, .5):
            if abs(nz - self.z) > 1e-5:
                self.z = nz
                moved = True
        return moved

    def update(self):
        if self.dead:
            return
        g = self.game
        if g.halted:
            return
        dt = time.dt
        player = g.player

        if self.flash_timer > 0:
            self.flash_timer -= dt
            if self.flash_timer <= 0:
                self.set_flash(False)
        if self.gun_flash_timer > 0:
            self.gun_flash_timer -= dt
            if self.gun_flash_timer <= 0:
                self.gun_flash.enabled = False

        dx = player.x - self.x
        dz = player.z - self.z
        dist = max(.001, math.hypot(dx, dz))
        to_player_angle = math.degrees(math.atan2(dx, dz))
        face_diff = (to_player_angle - self.rotation_y + 180) % 360 - 180

        moving = False
        turn_target = None

        if dist < AGGRO_RANGE:
            # speler in de buurt: draai naar hem toe, houd afstand, strafe
            turn_target = to_player_angle
            nx, nz = dx / dist, dz / dist
            if dist > 16:
                mdir, spd = (nx, nz), self.speed
            elif dist < 8:
                mdir, spd = (-nx, -nz), self.speed
            else:
                mdir, spd = (nz * self.strafe, -nx * self.strafe), self.speed * .7
            self.strafe_timer -= dt
            if self.strafe_timer <= 0:
                self.strafe *= -1
                self.strafe_timer = random.uniform(1.5, 4)
            moving = self.try_move(mdir[0], mdir[1], spd, dt)
            if not moving and 8 <= dist <= 16:
                self.strafe *= -1
        else:
            # willekeurig rondlopen
            self.wander_timer -= dt
            if self.wander_timer <= 0:
                self.wander_timer = random.uniform(2, 5)
                if random.random() < .25:
                    self.wander_dir = None            # even stilstaan
                else:
                    a = random.uniform(0, math.tau)
                    self.wander_dir = (math.sin(a), math.cos(a))
            if self.wander_dir:
                turn_target = math.degrees(math.atan2(self.wander_dir[0], self.wander_dir[1]))
                moving = self.try_move(self.wander_dir[0], self.wander_dir[1], self.speed * .6, dt)
                if not moving:
                    self.wander_timer = 0             # tegen een muur: kies nieuwe richting

        # draaien
        if turn_target is not None:
            diff = (turn_target - self.rotation_y + 180) % 360 - 180
            step = 320 * dt
            self.rotation_y += max(-step, min(step, diff))

        # loopanimatie
        if moving:
            self.walk_t += dt * 7
            swing = math.sin(self.walk_t) * 35
        else:
            swing = 0
        self.leg_l.rotation_x = swing
        self.leg_r.rotation_x = -swing
        self.arm_l.rotation_x = -swing * .8

        # healthbar naar de camera draaien
        cam = camera.world_position
        self.hp_bar.rotation_y = math.degrees(math.atan2(cam.x - self.x, cam.z - self.z)) - self.rotation_y

        # schieten
        self.fire_timer -= dt
        if self.fire_timer <= 0 and dist < SHOOT_RANGE and abs(face_diff) < 25:
            origin = self.muzzle.world_position
            target = player.position + Vec3(0, 1.0, 0)
            if not g.line_blocked(origin, target):
                self.shoot(origin, target)
                self.fire_timer = random.uniform(.8, 1.7)
            else:
                self.fire_timer = .35

    def shoot(self, origin, target):
        d = (target - origin).normalized()
        j = .045                                     # onnauwkeurigheid
        d = (d + Vec3(random.uniform(-j, j), random.uniform(-j, j), random.uniform(-j, j))).normalized()
        EnemyBullet(self.game, origin, d)
        self.gun_flash.enabled = True
        self.gun_flash_timer = .07


# ---------------------------------------------------------------------
#  Wapens
# ---------------------------------------------------------------------
class Weapon(Entity):
    """Een wapen met eigen 3D-model (hangt aan de camera), vuursnelheid,
    schade, magazijn, herlaadtijd en recoil-animatie."""

    def __init__(self, game, model_id, name, damage, fire_rate, mag, reserve, reload_time, rest,
                 auto=False, pellets=1, spread=.005, kind='hitscan', recoil_dist=.08, kick=4,
                 splash=0, falloff_range=0):
        super().__init__(parent=camera, position=Vec3(*rest), enabled=False)
        self.game = game
        self.weapon_name = name
        self.damage = damage
        self.fire_rate = fire_rate
        self.mag = mag
        self.max_reserve = reserve
        self.reload_time = reload_time
        self.rest = Vec3(*rest)
        self.auto = auto
        self.pellets = pellets
        self.spread = spread
        self.kind = kind
        self.recoil_dist = recoil_dist
        self.kick = kick
        self.splash = splash
        self.falloff_range = falloff_range

        self.ammo = mag
        self.reserve = reserve
        self.cooldown = 0
        self.reloading = False
        self.reload_t = 0
        self.recoil_v = 0.0
        self.draw_v = 0.0
        self.flash_t = 0

        self.muzzle = None
        self.build_model(model_id)
        self.flash = Entity(parent=self.muzzle, model='sphere', color=C('#ffd34d'), scale=.2, enabled=False)
        self.flash2 = Entity(parent=self.muzzle, model='sphere', color=C('#ffffff'), scale=.1, enabled=False)

    # ----- 3D modellen uit simpele vormen -----
    def add_part(self, shape, col, pos, scl, **kw):
        return Entity(parent=self, model=shape, color=C(col), position=pos, scale=scl, **kw)

    def build_model(self, model_id):
        P = self.add_part
        DARK, MID, WOOD, SKIN = '#26262a', '#4a4a52', '#6b4423', '#e0ac69'
        if model_id == 'pistol':
            P('cube', MID, (0, 0, 0), (.08, .1, .38))                          # slide
            P('cube', DARK, (0, -.12, -.1), (.07, .2, .09), rotation_x=-12)    # greep
            P('cube', DARK, (0, 0, .2), (.045, .045, .06))                     # loop
            P('cube', SKIN, (0, -.2, -.1), (.09, .09, .12))                    # hand
            self.muzzle = Entity(parent=self, position=(0, .01, .24))
        elif model_id == 'rifle':
            P('cube', DARK, (0, 0, 0), (.08, .11, .75))                        # body
            P('cube', MID, (0, -.01, .3), (.09, .09, .35))                     # handguard
            P('cube', MID, (0, -.03, -.5), (.07, .14, .28))                    # kolf
            P('cube', DARK, (0, -.17, 0), (.06, .22, .09), rotation_x=10)      # magazijn
            P('cube', MID, (0, .01, .65), (.035, .035, .4))                    # loop
            P('cube', DARK, (0, .085, .1), (.03, .06, .06))                    # vizier
            P('cube', SKIN, (0, -.08, .35), (.09, .08, .12))                   # hand
            self.muzzle = Entity(parent=self, position=(0, .01, .86))
        elif model_id == 'shotgun':
            P('cube', DARK, (0, .02, .1), (.07, .07, .95))                     # loop
            P('cube', MID, (0, -.05, .2), (.06, .06, .7))                      # magazijnbuis
            P('cube', WOOD, (0, -.07, .28), (.1, .08, .28))                    # pump
            P('cube', MID, (0, -.01, -.2), (.09, .14, .3))                     # slot
            P('cube', WOOD, (0, -.04, -.5), (.08, .13, .3))                    # kolf
            P('cube', SKIN, (0, -.12, .28), (.09, .07, .12))                   # hand
            self.muzzle = Entity(parent=self, position=(0, .02, .6))
        else:  # rocket launcher
            P('cube', '#556b2f', (0, 0, 0), (.16, .16, 1.0))                   # buis
            P('cube', DARK, (0, 0, .55), (.24, .24, .14))                      # voorkant
            P('cube', DARK, (0, 0, -.52), (.2, .2, .1))                        # achterkant
            P('cube', DARK, (0, -.17, -.05), (.07, .2, .08))                   # greep
            P('cube', MID, (0, .12, .1), (.04, .08, .2))                       # vizier
            P('cube', SKIN, (0, -.28, -.05), (.09, .08, .12))                  # hand
            self.muzzle = Entity(parent=self, position=(0, 0, .65))

    # ----- herladen -----
    def start_reload(self):
        if self.reloading or self.ammo >= self.mag or self.reserve <= 0:
            return
        self.reloading = True
        self.reload_t = self.reload_time

    def cancel_reload(self):
        self.reloading = False

    def finish_reload(self):
        take = min(self.mag - self.ammo, self.reserve)
        self.ammo += take
        self.reserve -= take
        self.reloading = False

    def refill(self):
        self.ammo = self.mag
        self.reserve = self.max_reserve
        self.reloading = False

    # ----- schieten -----
    def try_fire(self):
        g = self.game
        if g.halted or self.reloading or self.cooldown > 0:
            return False
        if self.ammo <= 0:
            self.cooldown = .35
            if self.reserve > 0:
                self.start_reload()
            else:
                g.show_message('Geen munitie! (Sandbox menu > Herstel HP+Ammo)', 1.5)
            return False
        self.ammo -= 1
        self.cooldown = self.fire_rate
        self.recoil_v = 1.0
        self.flash.enabled = True
        self.flash2.enabled = True
        s = random.uniform(.14, .26)
        self.flash.scale = s
        self.flash2.scale = s * .5
        self.flash.rotation_z = random.uniform(0, 360)
        self.flash_t = .05
        if self.kind == 'rocket':
            self.fire_rocket()
        else:
            self.fire_hitscan()
        if self.ammo == 0 and self.reserve > 0:
            self.start_reload()
        return True

    def fire_hitscan(self):
        g = self.game
        origin = camera.world_position
        base = camera.forward
        right, up = camera.right, camera.up
        muzzle_pos = self.muzzle.world_position
        for _ in range(self.pellets):
            d = (base + right * random.uniform(-self.spread, self.spread)
                 + up * random.uniform(-self.spread, self.spread)).normalized()
            hit = raycast(origin, d, distance=120, ignore=[g.player])
            if hit.hit:
                end = hit.world_point
                enemy = getattr(hit.entity, 'enemy', None)
                if enemy is not None and not enemy.dead:
                    dmg = self.damage * (2.0 if hit.entity is enemy.head else 1.0)
                    if self.falloff_range:
                        dmg *= max(.25, 1 - .75 * (hit.distance / self.falloff_range))
                    enemy.take_damage(dmg)
                    g.hit_marker(headshot=hit.entity is enemy.head)
                    g.spawn_impact(end, blood=True)
                else:
                    g.spawn_impact(end, blood=False)
            else:
                end = origin + d * 100
            g.spawn_tracer(muzzle_pos, end)

    def fire_rocket(self):
        g = self.game
        origin = camera.world_position
        hit = raycast(origin, camera.forward, distance=100, ignore=[g.player])
        muzzle_pos = self.muzzle.world_position
        if hit.hit and hit.distance > 3:
            direction = (hit.world_point - muzzle_pos).normalized()
        else:
            direction = camera.forward
        Rocket(g, muzzle_pos, direction, self.damage, self.splash)

    # ----- animatie -----
    def update(self):
        g = self.game
        if g.halted:
            return
        dt = time.dt
        if self.cooldown > 0:
            self.cooldown -= dt
        if self.flash_t > 0:
            self.flash_t -= dt
            if self.flash_t <= 0:
                self.flash.enabled = False
                self.flash2.enabled = False
        p = 0.0
        if self.reloading:
            self.reload_t -= dt
            if self.reload_t <= 0:
                self.finish_reload()
            else:
                prog = 1 - self.reload_t / self.reload_time
                p = math.sin(math.pi * max(0.0, min(1.0, prog)))
        self.recoil_v = max(0.0, self.recoil_v - dt * 7)
        self.draw_v = max(0.0, self.draw_v - dt * 4)
        self.position = self.rest + Vec3(0, -.35 * self.draw_v - .15 * p, -self.recoil_dist * self.recoil_v)
        self.rotation_x = -self.kick * self.recoil_v + 38 * p


# ---------------------------------------------------------------------
#  Het spel zelf: arena, speler, HUD, sandbox-menu
# ---------------------------------------------------------------------
class Game(Entity):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.enemies = []
        self.projectiles = []
        self.kills = 0
        self.menu_open = False
        self.player_dead = False
        self.player_hp = PLAYER_MAX_HP
        self.respawn_t = 0
        self.damage_flash = 0.0
        self.hit_t = 0.0
        self.msg_timer = 0.0
        self.auto_waves = False
        self.wave = 0
        self.wave_timer = 3.0
        self._cache = {}

        self.build_arena()

        self.player = FirstPersonController(position=PLAYER_START)
        self.player.speed = 6
        if hasattr(self.player, 'cursor'):          # eigen crosshair gebruiken
            self.player.cursor.color = color.clear

        self.build_weapons()
        self.build_hud()
        self.build_menu()

        for _ in range(5):
            self.spawn_enemy()
        self.show_message('Welkom! Druk op TAB of Q voor het Sandbox menu', 4)

    @property
    def halted(self):
        """True als de wereld gepauzeerd is (menu open of speler dood)."""
        return self.menu_open or self.player_dead

    @property
    def current_weapon(self):
        return self.weapons[self.weapon_index]

    # ------------------------------------------------------------------
    #  Arena
    # ------------------------------------------------------------------
    def build_arena(self):
        Sky()
        Entity(model='cube', texture='grass', texture_scale=(ARENA, ARENA),
               scale=(ARENA * 2 + 6, 1, ARENA * 2 + 6), position=(0, -.5, 0), collider='box')
        t = 1.0
        for (x, z, w, d) in [(0, ARENA + t / 2, 2 * ARENA + 2 * t, t),
                             (0, -ARENA - t / 2, 2 * ARENA + 2 * t, t),
                             (ARENA + t / 2, 0, t, 2 * ARENA),
                             (-ARENA - t / 2, 0, t, 2 * ARENA)]:
            Entity(model='cube', texture='brick', texture_scale=(max(w, d) / 3, WALL_H / 3),
                   position=(x, WALL_H / 2, z), scale=(w, WALL_H, d), collider='box')

        for sx in (-1, 1):                                   # pilaren
            for sz in (-1, 1):
                self.add_block(sx * 12, sz * 12, 2.5, 2.5, 7, 'brick')
        for (x, z, w, d, h) in [(0, 2, 9, .8, 2.4), (-21, -6, .8, 9, 2.4), (21, 6, .8, 9, 2.4),
                                (0, 18, 9, .8, 2.4), (-6, -14, 6, .8, 2.4)]:   # lage dekkingsmuren
            self.add_block(x, z, w, d, h, 'brick')
        for (x, z, s) in [(-5, 8, 2), (6, -6, 2), (-14, 0, 2.5), (14, -2, 2.5), (8, 12, 2),
                          (-10, 20, 2), (12, 22, 2), (-3, -8, 1.6), (2, 10, 1.6)]:  # kratten
            self.add_block(x, z, s, s, s, 'crate')

    def add_block(self, x, z, w, d, h, kind='brick', dynamic=False):
        if kind == 'crate':
            ent = Entity(model='cube', texture='white_cube', color=C('#b5742f'),
                         position=(x, h / 2, z), scale=(w, h, d), collider='box')
        else:
            ent = Entity(model='cube', texture='brick', texture_scale=(max(w, d) / 2, h / 2),
                         position=(x, h / 2, z), scale=(w, h, d), collider='box')
        b = Block(ent, w, d, h, dynamic)
        self.blocks.append(b)
        return b

    def blocked(self, x, z, r=.5):
        """Staat (x, z) in een obstakel (met marge r)?"""
        for b in self.blocks:
            if abs(x - b.x) < b.hw + r and abs(z - b.z) < b.hd + r:
                return True
        return False

    def in_block(self, p):
        for b in self.blocks:
            if abs(p.x - b.x) <= b.hw and abs(p.z - b.z) <= b.hd and p.y <= b.h:
                return True
        return False

    def point_blocked(self, p):
        """Voor projectielen: botst dit punt met vloer, arenagrens of obstakel?"""
        if p.y < 0 or abs(p.x) > ARENA or abs(p.z) > ARENA:
            return True
        return self.in_block(p)

    def line_blocked(self, a, b):
        d = b - a
        n = int(d.length() / .7)
        for i in range(1, n + 1):
            if self.in_block(a + d * (i / (n + 1))):
                return True
        return False

    # ------------------------------------------------------------------
    #  Wapens
    # ------------------------------------------------------------------
    def build_weapons(self):
        specs = [
            dict(model_id='pistol', name='Pistool', damage=26, fire_rate=.26, mag=12, reserve=96,
                 reload_time=1.0, auto=False, spread=.004, recoil_dist=.10, kick=5, rest=(.28, -.26, .7)),
            dict(model_id='rifle', name='Assault Rifle', damage=13, fire_rate=.085, mag=30, reserve=210,
                 reload_time=1.7, auto=True, spread=.012, recoil_dist=.06, kick=2.5, rest=(.3, -.28, .85)),
            dict(model_id='shotgun', name='Shotgun', damage=12, pellets=8, fire_rate=.8, mag=6, reserve=42,
                 reload_time=2.2, auto=False, spread=.055, recoil_dist=.25, kick=9, rest=(.3, -.27, .95),
                 falloff_range=26),
            dict(model_id='rocket', name='Rocket Launcher', kind='rocket', damage=130, splash=6,
                 fire_rate=1.1, mag=1, reserve=14, reload_time=1.9, recoil_dist=.3, kick=7,
                 rest=(.32, -.3, 1.0)),
        ]
        self.weapons = [Weapon(self, **s) for s in specs]
        self.weapon_index = 0
        self.weapons[0].enabled = True

    def select_weapon(self, i):
        if i == self.weapon_index:
            return
        self.current_weapon.cancel_reload()
        self.current_weapon.enabled = False
        self.weapon_index = i
        w = self.current_weapon
        w.enabled = True
        w.draw_v = 1.0
        self.refresh_menu()

    def refill_ammo(self):
        for w in self.weapons:
            w.refill()

    # ------------------------------------------------------------------
    #  Effecten
    # ------------------------------------------------------------------
    def spawn_tracer(self, a, b):
        d = (b - a).length()
        if d < .2:
            return
        t = Entity(model='cube', color=C('#ffe9a0'), position=(a + b) * .5)
        t.look_at(b)
        t.scale = Vec3(.02, .02, d)
        destroy(t, delay=.04)

    def spawn_impact(self, point, blood=False):
        if blood:
            e = Entity(model='sphere', color=C('#c01010'), position=point, scale=.2)
            destroy(e, delay=.15)
        else:
            hole = Entity(model='sphere', color=C('#121212'), position=point, scale=.08)
            destroy(hole, delay=8)
            spark = Entity(model='sphere', color=C('#ffd34d'), position=point, scale=.14)
            destroy(spark, delay=.06)

    def explode(self, pos, radius, damage):
        Explosion(pos, radius)
        for e in list(self.enemies):
            d = (e.position + Vec3(0, 1, 0) - pos).length()
            if d < radius:
                e.take_damage(damage * (1 - .8 * d / radius))
        d = (self.player.position + Vec3(0, 1, 0) - pos).length()
        if d < radius:
            self.damage_player(25 * (1 - d / radius))

    def hit_marker(self, headshot=False):
        self.hit_t = .12
        for p in self.cross_parts:
            p.color = C('#ff3030')
        if headshot:
            self.show_message('HEADSHOT!', .8)

    # ------------------------------------------------------------------
    #  Speler: schade, dood, respawn
    # ------------------------------------------------------------------
    def damage_player(self, amount):
        if self.player_dead:
            return
        self.player_hp -= amount
        self.damage_flash = min(1.0, self.damage_flash + .45)
        if self.player_hp <= 0:
            self.player_hp = 0
            self.kill_player()

    def kill_player(self):
        self.player_dead = True
        self.respawn_t = 3.0
        if self.menu_open:
            self.menu_open = False
            self.menu.enabled = False
        self.player.enabled = False
        mouse.locked = False
        self.crosshair.enabled = False
        self.damage_flash = 1.0

    def respawn(self):
        self.player_dead = False
        self.player_hp = PLAYER_MAX_HP
        self.damage_flash = 0
        self.clear_projectiles()
        p = self.player
        p.position = PLAYER_START
        p.rotation_y = 0
        if hasattr(p, 'camera_pivot'):
            p.camera_pivot.rotation_x = 0
        p.enabled = True
        mouse.locked = True
        self.crosshair.enabled = True
        self.refill_ammo()
        self.dead_text.text = ''
        self.show_message('Respawn!', 1.5)

    # ------------------------------------------------------------------
    #  Sandbox-acties
    # ------------------------------------------------------------------
    def spawn_enemy(self):
        px, pz = self.player.x, self.player.z
        x, z = 20, 20
        for _ in range(60):
            cx = random.uniform(-ARENA + 3, ARENA - 3)
            cz = random.uniform(-ARENA + 3, ARENA - 3)
            if self.blocked(cx, cz, 1.2) or math.hypot(cx - px, cz - pz) < 12:
                continue
            x, z = cx, cz
            break
        e = Enemy(self, Vec3(x, 0, z))
        self.enemies.append(e)
        return e

    def spawn_enemies(self, n):
        for _ in range(n):
            self.spawn_enemy()
        self.show_message(f'+{n} vijand(en) gespawnd', 1.5)

    def spawn_obstacle(self, kind):
        p = self.player
        f = Vec3(p.forward.x, 0, p.forward.z)
        if f.length() < .01:
            f = Vec3(0, 0, 1)
        f = f.normalized()
        limit = ARENA - 3
        x = max(-limit, min(limit, p.x + f.x * 7))
        z = max(-limit, min(limit, p.z + f.z * 7))
        if math.hypot(x - p.x, z - p.z) < 4:
            self.show_message('Geen ruimte hier - draai je om', 1.5)
            return
        if kind == 'crate':
            s = random.choice([1.6, 2.0, 2.5])
            self.add_block(x, z, s, s, s, 'crate', dynamic=True)
            self.show_message('Krat geplaatst', 1.2)
        else:
            if abs(f.x) > abs(f.z):
                w, d = .8, 6
            else:
                w, d = 6, .8
            self.add_block(x, z, w, d, 3.0, 'brick', dynamic=True)
            self.show_message('Muur geplaatst', 1.2)

    def heal_all(self):
        self.player_hp = PLAYER_MAX_HP
        self.refill_ammo()
        self.show_message('HP en munitie hersteld', 1.5)

    def clear_enemies(self):
        for e in list(self.enemies):
            destroy(e)
        self.enemies.clear()
        self.clear_projectiles()
        self.show_message('Alle vijanden verwijderd', 1.5)

    def clear_projectiles(self):
        for p in list(self.projectiles):
            p.remove()

    def clear_obstacles(self):
        keep = []
        for b in self.blocks:
            if b.dynamic:
                destroy(b.entity)
            else:
                keep.append(b)
        self.blocks = keep
        self.show_message('Geplaatste obstakels verwijderd', 1.5)

    def toggle_waves(self):
        self.auto_waves = not self.auto_waves
        self.wave_btn.text = 'Auto-golven: AAN' if self.auto_waves else 'Auto-golven: UIT'
        self.wave_timer = 2.0

    # ------------------------------------------------------------------
    #  HUD
    # ------------------------------------------------------------------
    def build_hud(self):
        L = -window.aspect_ratio * .5 + .03
        R = -L
        # crosshair
        self.crosshair = Entity(parent=camera.ui, z=-.02)
        self.cross_parts = []
        for pos, scl in [((-.017, 0), (.014, .0025)), ((.017, 0), (.014, .0025)),
                         ((0, .017), (.0025, .014)), ((0, -.017), (.0025, .014)),
                         ((0, 0), (.004, .004))]:
            self.cross_parts.append(Entity(parent=self.crosshair, model='quad', color=color.white,
                                           position=pos, scale=scl))
        # HP
        self.hp_text = Text(text='HP 100', position=(L, -.395), origin=(-.5, -.5), scale=1.8, color=color.white)
        self.hp_bar_bg = Entity(parent=camera.ui, model='quad', color=C('#3a0d0d'),
                                scale=(.3, .025), position=(L, -.44), origin=(-.5, -.5), z=0)
        self.hp_bar_fg = Entity(parent=camera.ui, model='quad', color=C('#33d14a'),
                                scale=(.3, .025), position=(L, -.44), origin=(-.5, -.5), z=-.01)
        # wapen + munitie
        self.weapon_text = Text(text='', position=(R, -.35), origin=(.5, -.5), scale=1.4, color=C('#ffd34d'))
        self.ammo_text = Text(text='', position=(R, -.45), origin=(.5, -.5), scale=2.4, color=color.white)
        # info
        self.info_text = Text(text='', position=(L, .47), origin=(-.5, .5), scale=1.3, color=color.white)
        self.hint_text = Text(text='TAB / Q: sandbox menu  |  R: herladen  |  1-4: wapens', position=(0, -.485),
                              origin=(0, -.5), scale=.9, color=C('#cccccc'))
        self.msg_text = Text(text='', position=(0, .46), origin=(0, 0), scale=1.5, color=C('#ffe066'))
        self.dead_text = Text(text='', position=(0, .08), origin=(0, 0), scale=3, color=C('#ff3030'))
        # rode flits bij schade
        self.overlay = Entity(parent=camera.ui, model='quad', color=rgba(1, 0, 0, 0), scale=(4, 2), z=1,
                              enabled=False)

    def set_text(self, key, text_entity, value):
        if self._cache.get(key) != value:
            self._cache[key] = value
            text_entity.text = value

    def show_message(self, text, duration=2.0):
        self.msg_text.text = text
        self._cache.pop('msg', None)
        self.msg_timer = duration

    def update_hud(self):
        w = self.current_weapon
        hp = int(math.ceil(self.player_hp))
        self.set_text('hp', self.hp_text, f'HP {hp}')
        ratio = max(0.0, min(1.0, self.player_hp / PLAYER_MAX_HP))
        self.hp_bar_fg.scale_x = max(.0001, .3 * ratio)
        self.set_text('weapon', self.weapon_text, w.weapon_name)
        if w.reloading:
            ammo = 'HERLADEN...'
        else:
            ammo = f'{w.ammo} / {w.reserve}'
        self.set_text('ammo', self.ammo_text, ammo)
        self.set_text('info', self.info_text, f'Kills: {self.kills}    Vijanden: {len(self.enemies)}')

    # ------------------------------------------------------------------
    #  Sandbox-menu
    # ------------------------------------------------------------------
    def build_menu(self):
        self.menu = Entity(parent=camera.ui)
        Entity(parent=self.menu, model='quad', color=rgba(.05, .09, .13, .93), scale=(.8, .9), z=.1)

        def label(text, y, col='#9ad1ff', scale=1.1):
            Text(text, parent=self.menu, origin=(0, 0), position=(0, y), scale=scale, color=C(col))

        def button(text, x, y, fn, col='#2d6cdf'):
            return Button(parent=self.menu, text=text, position=(x, y), scale=(.34, .062),
                          color=C(col), on_click=fn)

        label('SANDBOX MENU', .39, '#ffffff', 1.8)
        label('WAPENS', .32)
        self.weapon_buttons = []
        names = ['Pistool [1]', 'Assault Rifle [2]', 'Shotgun [3]', 'Rocket Launcher [4]']
        for i, nm in enumerate(names):
            x = -.19 if i % 2 == 0 else .19
            y = .255 if i < 2 else .183
            self.weapon_buttons.append(button(nm, x, y, lambda i=i: self.select_weapon(i)))

        label('SPAWNEN', .11)
        button('+ Vijand', -.19, .045, lambda: self.spawn_enemies(1), '#b03a2e')
        button('+ 5 Vijanden', .19, .045, lambda: self.spawn_enemies(5), '#b03a2e')
        button('+ Krat', -.19, -.027, lambda: self.spawn_obstacle('crate'), '#a0692a')
        button('+ Muur', .19, -.027, lambda: self.spawn_obstacle('wall'), '#a0692a')

        label('BEHEER', -.10)
        button('Herstel HP+Ammo', -.19, -.165, self.heal_all, '#1e8449')
        button('Wis vijanden', .19, -.165, self.clear_enemies, '#7d3c98')
        button('Wis obstakels', -.19, -.237, self.clear_obstacles, '#7d3c98')
        self.wave_btn = button('Auto-golven: UIT', .19, -.237, self.toggle_waves, '#5d6d7e')
        button('Menu sluiten', -.19, -.309, lambda: self.set_menu(False), '#566573')
        button('Spel afsluiten', .19, -.309, application.quit, '#922b21')
        label('TAB / Q = menu open/dicht', -.385, '#aaaaaa', .9)

        self.menu.enabled = False
        self.refresh_menu()

    def refresh_menu(self):
        for i, b in enumerate(self.weapon_buttons):
            b.color = C('#e0a020') if i == self.weapon_index else C('#2d6cdf')

    def set_menu(self, state):
        if state and self.player_dead:
            return
        self.menu_open = state
        self.menu.enabled = state
        self.player.enabled = not state       # pauzeert besturing en muis-look
        mouse.locked = not state              # cursor zichtbaar in het menu
        self.crosshair.enabled = not state
        if state:
            self.refresh_menu()

    # ------------------------------------------------------------------
    #  Input & update
    # ------------------------------------------------------------------
    def input(self, key):
        if key in ('tab', 'q'):
            self.set_menu(not self.menu_open)
            return
        if self.halted:
            return
        if key == 'r':
            self.current_weapon.start_reload()
        elif key in ('1', '2', '3', '4'):
            self.select_weapon(int(key) - 1)
        elif key == 'scroll up':
            self.select_weapon((self.weapon_index + 1) % len(self.weapons))
        elif key == 'scroll down':
            self.select_weapon((self.weapon_index - 1) % len(self.weapons))
        elif key == 'left mouse down' and not self.current_weapon.auto:
            self.current_weapon.try_fire()

    def update(self):
        dt = time.dt

        # berichten
        if self.msg_timer > 0:
            self.msg_timer -= dt
            if self.msg_timer <= 0:
                self.msg_text.text = ''

        # rode schade-flits
        if self.damage_flash > 0:
            self.damage_flash = max(0.0, self.damage_flash - dt * 1.4)
            self.overlay.enabled = True
            self.overlay.color = rgba(1, 0, 0, min(1.0, self.damage_flash) * .55)
        elif self.overlay.enabled:
            self.overlay.enabled = False

        # hitmarker herstellen
        if self.hit_t > 0:
            self.hit_t -= dt
            if self.hit_t <= 0:
                for p in self.cross_parts:
                    p.color = color.white

        # dood -> respawn timer
        if self.player_dead:
            self.respawn_t -= dt
            self.dead_text.text = f'JE BENT DOOD\nRespawn over {max(1, math.ceil(self.respawn_t))}...'
            if self.respawn_t <= 0:
                self.respawn()

        self.update_hud()
        if self.halted:
            return

        # uit de wereld gevallen? terug naar start
        if self.player.y < -20:
            self.player.position = PLAYER_START

        # automatisch vuur (ingedrukt houden)
        w = self.current_weapon
        if w.auto and held_keys['left mouse']:
            w.try_fire()

        # nieuwe golf als alle vijanden dood zijn
        if self.auto_waves:
            if not self.enemies:
                self.wave_timer -= dt
                if self.wave_timer <= 0:
                    self.wave += 1
                    n = min(12, 3 + self.wave)
                    for _ in range(n):
                        self.spawn_enemy()
                    self.show_message(f'Golf {self.wave}!  ({n} vijanden)', 2.5)
                    self.wave_timer = 3.0
            else:
                self.wave_timer = 3.0


# ---------------------------------------------------------------------
#  Start
# ---------------------------------------------------------------------
if __name__ == '__main__':
    app = Ursina(title='Arena Sandbox FPS', borderless=False)
    window.exit_button.visible = False
    game = Game()
    app.run()


# =====================================================================
#  BESTURING
# =====================================================================
#  W A S D ........ lopen
#  Spatie ......... springen
#  Muis ........... rondkijken / richten
#  Linkermuisknop . schieten (Assault Rifle: ingedrukt houden)
#  R .............. herladen
#  1 2 3 4 ........ wapen kiezen (of scrollwiel): Pistool, Assault Rifle,
#                   Shotgun, Rocket Launcher
#  TAB of Q ....... Sandbox menu openen/sluiten (muiscursor verschijnt, de
#                   wereld pauzeert). In het menu kun je:
#                     - wapens kiezen
#                     - vijanden spawnen (+1 of +5)
#                     - kratten en muren neerzetten (voor je neus)
#                     - HP + munitie herstellen
#                     - alle vijanden / geplaatste obstakels wissen
#                     - automatische vijandgolven aan/uit zetten
#                     - het spel afsluiten
#
#  Tips: een headshot doet dubbele schade. De Shotgun is sterk van dichtbij,
#  de Rocket Launcher heeft splash damage (let op: je raakt zelf ook
#  gewond als je te dichtbij ontploft). Vijanden schieten alleen als ze je
#  kunnen zien, dus kratten en pilaren geven echte dekking.
# =====================================================================
