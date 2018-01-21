from gibson.envs.env_modalities import CameraRobotEnv, SensorRobotEnv
from gibson.envs.env_bases import *
from gibson.core.physics.robot_locomotors import Husky, HuskyClimber, HuskyHighCamera
from transforms3d import quaternions
from gibson import configs
import os
import numpy as np
import sys
import pybullet as p
from gibson.core.physics.scene_stadium import SinglePlayerStadiumScene
import pybullet_data

HUSKY_TIMESTEP  = 1.0/(4 * 22)
HUSKY_FRAMESKIP = 4

tracking_camera = {
    'yaw': 20,  # demo: living room, stairs
    #'yaw'; 30,   # demo: kitchen
    'z_offset': 0.5,
    'distance': 1,
    'pitch': -20
    # 'pitch': -24  # demo: stairs
}

tracking_camera_top = {
    'yaw': 20,  # demo: living room, stairs
    #'yaw'; 30,   # demo: kitchen
    'z_offset': 0.5,
    'distance': 1,
    'pitch': -20
    # 'pitch': -24  # demo: stairs
}

class HuskyNavigateEnv(CameraRobotEnv):
    """Specfy navigation reward
    """
    def __init__(
            self, 
            human=True, 
            timestep=HUSKY_TIMESTEP, 
            frame_skip=HUSKY_FRAMESKIP, 
            is_discrete=False, 
            mode="RGBD", 
            use_filler=True, 
            gpu_count=0, 
            resolution="NORMAL"):
        self.human = human
        self.model_id = configs.NAVIGATE_MODEL_ID
        self.timestep = timestep
        self.frame_skip = frame_skip
        self.resolution = resolution
        self.tracking_camera = tracking_camera
        target_orn, target_pos   = configs.TASK_POSE[configs.NAVIGATE_MODEL_ID]["navigate"][-1]
        initial_orn, initial_pos = configs.TASK_POSE[configs.NAVIGATE_MODEL_ID]["navigate"][0]
        self.total_reward = 0
        self.total_frame = 0
        
        CameraRobotEnv.__init__(
            self, 
            mode, 
            gpu_count, 
            scene_type="building", 
            use_filler=use_filler)
        self.robot_introduce(HuskyHighCamera(
            is_discrete=is_discrete, 
            initial_pos=initial_pos,
            initial_orn=initial_orn,
            target_pos=target_pos,
            resolution=resolution,
            mode=mode))
        self.scene_introduce()

    def calc_rewards_and_done(self, a, state):
        done = self._termination(state)
        rewards = self._rewards(a)
        debugmode = 0
        if debugmode:
            print("Frame %f reward %f" % (self.nframe, sum(rewards)))

        self.total_reward = self.total_reward + sum(rewards)
        self.total_frame = self.total_frame + 1
        #print(self.total_frame, self.total_reward)
        return rewards, done

    def _rewards(self, action=None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_potential()
        progress = float(self.potential - potential_old)

        feet_collision_cost = 0.0
        for i, f in enumerate(
                self.robot.feet):  # TODO: Maybe calculating feet contacts could be done within the robot code
            # print(f.contact_list())
            contact_ids = set((x[2], x[4]) for x in f.contact_list())
            # print("CONTACT OF '%d' WITH %d" % (contact_ids, ",".join(contact_names)) )
            if (self.ground_ids & contact_ids):
                # see Issue 63: https://github.com/openai/roboschool/issues/63
                # feet_collision_cost += self.foot_collision_cost
                self.robot.feet_contact[i] = 1.0
            else:
                self.robot.feet_contact[i] = 0.0
        # print(self.robot.feet_contact)

        electricity_cost  = self.electricity_cost  * float(np.abs(a*self.robot.joint_speeds).mean())  # let's assume we 
        electricity_cost  += self.stall_torque_cost * float(np.square(a).mean())


        steering_cost = self.robot.steering_cost(a)
        debugmode = 0
        if debugmode:
            print("steering cost", steering_cost)

        #alive = len(self.robot.parts['top_bumper_link'].contact_list())
        #if alive == 0:
        #    alive_score = 0.1
        #else:
        #    alive_score = -0.1

        wall_contact = [pt for pt in self.robot.parts['base_link'].contact_list() if pt[6][2] > 0.15]
        wall_collision_cost = self.wall_collision_cost * len(wall_contact)

        joints_at_limit_cost = float(self.joints_at_limit_cost * self.robot.joints_at_limit)
        close_to_goal = 0
        if self.robot.is_close_to_goal():
            close_to_goal = 0.5

        angle_cost = self.robot.angle_cost()

        obstacle_penalty = 0
        if CALC_OBSTACLE_PENALTY and self._require_camera_input:
            obstacle_penalty = get_obstacle_penalty(self.robot, self.render_depth)

        debugmode = 0
        if debugmode:
            print("angle cost", angle_cost)

        debugmode = 0
        if (debugmode):
            print("Wall contact points", len(wall_contact))
            print("Collision cost", wall_collision_cost)
            print("electricity_cost", electricity_cost)
            print("close to goal", close_to_goal)
            #print("progress")
            #print(progress)
            #print("electricity_cost")
            #print(electricity_cost)
            #print("joints_at_limit_cost")
            #print(joints_at_limit_cost)
            #print("feet_collision_cost")
            #print(feet_collision_cost)

        rewards = [
            #alive,
            progress,
            #wall_collision_cost,
            close_to_goal,
            steering_cost,
            angle_cost,
            obstacle_penalty
            #electricity_cost,
            #joints_at_limit_cost,
            #feet_collision_cost
        ]
        return rewards

    def _termination(self, state=None, debugmode=False):
        alive = float(self.robot.alive_bonus(state[0] + self.robot.initial_z, self.robot.body_rpy[
            1]))  # state[0] is body height above ground, body_rpy[1] is pitch
        
        alive = len(self.robot.parts['top_bumper_link'].contact_list()) == 0

        done = not alive or self.nframe > 250 or self.robot.body_xyz[2] < 0
        #done = alive < 0
        if not np.isfinite(state).all():
            print("~INF~", state)
            done = True
        if done:
            print("Episode reset")
        return done

    def _flag_reposition(self):
        walk_target_x = self.robot.walk_target_x
        walk_target_y = self.robot.walk_target_y

        self.flag = None
        if self.human and not configs.DISPLAY_UI:
            self.visual_flagId = p.createVisualShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.5, 0.5, 0.5], rgbaColor=[1, 0, 0, 0.7])
            self.last_flagId = p.createMultiBody(baseVisualShapeIndex=self.visual_flagId, baseCollisionShapeIndex=-1, basePosition=[walk_target_x, walk_target_y, 0.5])

    def  _reset(self):
        self.total_frame = 0
        self.total_reward = 0
        obs = CameraRobotEnv._reset(self)
        self._flag_reposition()
        return obs


class HuskyClimbEnv(CameraRobotEnv):
    """Specfy navigation reward
    """
    def __init__(
            self, 
            human=True, 
            timestep=HUSKY_TIMESTEP, 
            frame_skip=HUSKY_FRAMESKIP, 
            is_discrete=False, 
            mode="RGBD", 
            use_filler=True, 
            gpu_count=0, 
            resolution="NORMAL"):
        self.human = human
        self.model_id = configs.CLIMB_MODEL_ID
        self.timestep = timestep
        self.frame_skip = frame_skip
        self.resolution = resolution
        self.tracking_camera = tracking_camera
        target_orn, target_pos   = configs.TASK_POSE[configs.CLIMB_MODEL_ID]["climb"][-1]
        initial_orn, initial_pos = configs.TASK_POSE[configs.CLIMB_MODEL_ID]["climb"][0]
        self.total_reward = 0
        self.total_frame = 0

        CameraRobotEnv.__init__(
            self, 
            mode, 
            gpu_count, 
            scene_type="building", 
            use_filler=use_filler)
        self.robot_introduce(HuskyClimber(
            is_discrete=is_discrete, 
            initial_pos=initial_pos,
            initial_orn=initial_orn,
            target_pos=target_pos,
            resolution=resolution))
        self.scene_introduce()

        
    def calc_rewards_and_done(self, a, state):
        done = self._termination(state)
        rewards = self._rewards(a)
        print("Frame %f reward %f" % (self.nframe, sum(rewards)))

        self.total_reward = self.total_reward + sum(rewards)
        self.total_frame = self.total_frame + 1
        #print(self.total_frame, self.total_reward)
        return rewards, done

    def _rewards(self, action=None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_potential()
        progress = float(self.potential - potential_old)

        feet_collision_cost = 0.0
        for i, f in enumerate(
                self.robot.feet):  # TODO: Maybe calculating feet contacts could be done within the robot code
            # print(f.contact_list())
            contact_ids = set((x[2], x[4]) for x in f.contact_list())
            # print("CONTACT OF '%d' WITH %d" % (contact_ids, ",".join(contact_names)) )
            if (self.ground_ids & contact_ids):
                # see Issue 63: https://github.com/openai/roboschool/issues/63
                # feet_collision_cost += self.foot_collision_cost
                self.robot.feet_contact[i] = 1.0
            else:
                self.robot.feet_contact[i] = 0.0
        # print(self.robot.feet_contact)

        electricity_cost  = self.electricity_cost  * float(np.abs(a*self.robot.joint_speeds).mean())  # let's assume we 
        electricity_cost  += self.stall_torque_cost * float(np.square(a).mean())
        #alive = len(self.robot.parts['top_bumper_link'].contact_list())
        #if alive == 0:
        #    alive_score = 0.1
        #else:
        #    alive_score = -0.1

        wall_contact = [pt for pt in self.robot.parts['base_link'].contact_list() if pt[6][2] > 0.15]
        wall_collision_cost = self.wall_collision_cost * len(wall_contact)

        joints_at_limit_cost = float(self.joints_at_limit_cost * self.robot.joints_at_limit)
        close_to_goal = 0
        if self.robot.is_close_to_goal():
            close_to_goal = 0.5

        obstacle_penalty = 0
        if CALC_OBSTACLE_PENALTY and self._require_camera_input:
            obstacle_penalty =get_obstacle_penalty(self.robot, self.render_depth)

        debugmode = 0
        if (debugmode):
            print("progress")
            print(progress)
            #print("electricity_cost")
            #print(electricity_cost)
            #print("joints_at_limit_cost")
            #print(joints_at_limit_cost)
            #print("feet_collision_cost")
            #print(feet_collision_cost)

        rewards = [
            #alive,
            progress,
            #wall_collision_cost,
            close_to_goal,
            obstacle_penalty
            #electricity_cost,
            #joints_at_limit_cost,
            #feet_collision_cost
        ]
        return rewards

    def _termination(self, state=None, debugmode=False):
        alive = float(self.robot.alive_bonus(state[0] + self.robot.initial_z, self.robot.body_rpy[
            1]))  # state[0] is body height above ground, body_rpy[1] is pitch
        
        alive = len(self.robot.parts['top_bumper_link'].contact_list()) == 0

        done = not alive or self.nframe > 1000
        #done = alive < 0
        if not np.isfinite(state).all():
            print("~INF~", state)
            done = True
        if done:
            print("Episode reset")
        return done

    def _flag_reposition(self):
        walk_target_x = self.robot.walk_target_x
        walk_target_y = self.robot.walk_target_y
        walk_target_z = self.robot.walk_target_z
        #walk_target_x = self.robot.initial_pos[0]
        #walk_target_y = self.robot.initial_pos[1]
        #walk_target_z = self.robot.initial_pos[2]

        self.flag = None
        if self.human and not configs.DISPLAY_UI:
            self.visual_flagId = p.createVisualShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.5, 0.5, 0.5], rgbaColor=[1, 0, 0, 0.7])
            self.last_flagId = p.createMultiBody(baseVisualShapeIndex=self.visual_flagId, baseCollisionShapeIndex=-1, basePosition=[walk_target_x, walk_target_y, walk_target_z])
        #print("Placing the flag at", p.getBasePositionAndOrientation(self.last_flagId))

    def  _reset(self):
        self.total_frame = 0
        self.total_reward = 0
        obs = CameraRobotEnv._reset(self)
        self._flag_reposition()
        return obs



class HuskyFlagRunEnv(CameraRobotEnv):
    """Specfy flagrun reward
    """
    def __init__(self, human=True, timestep=HUSKY_TIMESTEP,
                 frame_skip=HUSKY_FRAMESKIP, is_discrete=False, 
                 gpu_count=0, mode="SENSOR", resolution="NORMAL"):
        self.human = human
        self.timestep = timestep
        self.frame_skip = frame_skip
        ## Mode initialized with mode=SENSOR
        self.model_id = configs.FETCH_MODEL_ID
        self.tracking_camera = tracking_camera
        initial_pos, initial_orn = [0, 0, 0.3], [0, 0, 0, 1]
        self.flag_timeout = 1

        CameraRobotEnv.__init__(self, mode=mode, gpu_count=gpu_count, scene_type="stadium")
        self.robot_introduce(Husky(
            is_discrete=is_discrete, 
            initial_pos=initial_pos,
            initial_orn=initial_orn, 
            resolution=resolution))
        self.scene_introduce()

        if self.human:
            self.visualid = p.createVisualShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.5, 0.5, 0.5], rgbaColor=[1, 0, 0, 0.7])
        self.lastid = None
        
    def _reset(self):
        obs = CameraRobotEnv._reset(self)
        return obs

    def _flag_reposition(self):
        self.walk_target_x = self.np_random.uniform(low=-self.scene.stadium_halflen,
                                                    high=+self.scene.stadium_halflen)
        self.walk_target_y = self.np_random.uniform(low=-self.scene.stadium_halfwidth,
                                                    high=+self.scene.stadium_halfwidth)

        more_compact = 0.5  # set to 1.0 whole football field
        self.walk_target_x *= more_compact
        self.walk_target_y *= more_compact

        self.flag = None
        #self.flag = self.scene.cpp_world.debug_sphere(self.walk_target_x, self.walk_target_y, 0.2, 0.2, 0xFF8080)
        self.flag_timeout = 600 / self.scene.frame_skip
        #print('targetxy', self.flagid, self.walk_target_x, self.walk_target_y, p.getBasePositionAndOrientation(self.flagid))
        #p.resetBasePositionAndOrientation(self.flagid, posObj = [self.walk_target_x, self.walk_target_y, 0.5], ornObj = [0,0,0,0])
        if self.human and not configs.DISPLAY_UI:
            if self.lastid:
                p.removeBody(self.lastid)

            self.lastid = p.createMultiBody(baseVisualShapeIndex=self.visualid, baseCollisionShapeIndex=-1, basePosition=[self.walk_target_x, self.walk_target_y, 0.5])

        self.robot.walk_target_x = self.walk_target_x
        self.robot.walk_target_y = self.walk_target_y

    def calc_rewards_and_done(self, a, state):
        done = self._termination(state)
        rewards = self._rewards(a)

        return rewards, done


    def _rewards(self, action=None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_potential()
        progress = float(self.potential - potential_old)

        if not a is None:
            electricity_cost = self.electricity_cost * float(np.abs(
                a * self.robot.joint_speeds).mean())  # let's assume we have DC motor with controller, and reverse current braking
            electricity_cost += self.stall_torque_cost * float(np.square(a).mean())
        else:
            electricity_cost = 0

        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        if alive == 0:
            alive_score = 0.1
        else:
            alive_score = -0.1

        joints_at_limit_cost = float(self.joints_at_limit_cost * self.robot.joints_at_limit)

        obstacle_penalty = 0
        if CALC_OBSTACLE_PENALTY and self._require_camera_input:
            obstacle_penalty =get_obstacle_penalty(self.robot, self.render_depth)

        debugmode = 0
        if (debugmode):
            print("progress")
            print(progress)

        rewards = [
            alive_score,
            progress,
            obstacle_penalty
        ]
        return rewards

    def _termination(self, state=None, debugmode=False):
        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        if (debugmode):
            print("alive=")
            print(alive)
        done = alive > 0 or self.nframe > 500
        if not np.isfinite(state).all():
            print("~INF~", state)
            done = True
        return done

    def _step(self, a):
        state, reward, done, meta = CameraRobotEnv._step(self, a)
        #print('dist %.1f' % self.robot.walk_target_dist)
        if self.flag_timeout <= 0 or self.robot.walk_target_dist <= 0.5:
            self._flag_reposition()
        self.flag_timeout -= 1

        return state, reward, done, meta


class HuskyFetchEnv(CameraRobotEnv):
    """Specfy flagrun reward
    """
    def __init__(self, human=True, timestep=HUSKY_TIMESTEP,
                 frame_skip=HUSKY_FRAMESKIP, is_discrete=False,
                 gpu_count=0, scene_type="building", mode = 'SENSOR', use_filler=True, resolution = "NORMAL"):

        target_orn, target_pos = configs.TASK_POSE[configs.FETCH_MODEL_ID]["fetch"][-1]
        initial_orn, initial_pos = configs.TASK_POSE[configs.FETCH_MODEL_ID]["fetch"][0]
        
        self.human = human
        self.timestep = timestep
        self.frame_skip = frame_skip
        self.model_id = configs.FETCH_MODEL_ID
        ## Mode initialized with mode=SENSOR
        self.tracking_camera = tracking_camera
        self.flag_timeout = 1
        self.visualid = -1
        self.lastid = None
        
        CameraRobotEnv.__init__(
            self,
            mode,
            gpu_count,
            scene_type="building",
            use_filler=use_filler)
        self.robot_introduce(Husky(
            is_discrete,
            initial_pos=initial_pos,
            initial_orn=initial_orn,
            target_pos=target_pos,
            resolution=resolution))
        self.scene_introduce()

        if self.human:
            self.visualid = p.createVisualShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.2, 0.2, 0.2], rgbaColor=[1, 0, 0, 0.7])
        self.colisionid = p.createCollisionShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.2, 0.5, 0.2])

        self.lastid = None
        self.obstacle_dist = 100


    def _reset(self):
        obs = CameraRobotEnv._reset(self)
        return obs

    def _flag_reposition(self):
        #self.walk_target_x = self.np_random.uniform(low=-self.scene.stadium_halflen,
        #                                            high=+self.scene.stadium_halflen)
        #self.walk_target_y = self.np_random.uniform(low=-self.scene.stadium_halfwidth,
        #                                            high=+self.scene.stadium_halfwidth)
        force_x = self.np_random.uniform(-300,300)
        force_y = self.np_random.uniform(-300, 300)

        more_compact = 0.5  # set to 1.0 whole football field
        #self.walk_target_x *= more_compact
        #self.walk_target_y *= more_compact

        startx, starty, _ = self.robot.body_xyz


        self.flag = None
        #self.flag = self.scene.cpp_world.debug_sphere(self.walk_target_x, self.walk_target_y, 0.2, 0.2, 0xFF8080)
        self.flag_timeout = 600 / self.scene.frame_skip
        #print('targetxy', self.flagid, self.walk_target_x, self.walk_target_y, p.getBasePositionAndOrientation(self.flagid))
        #p.resetBasePositionAndOrientation(self.flagid, posObj = [self.walk_target_x, self.walk_target_y, 0.5], ornObj = [0,0,0,0])
        if self.lastid:
            p.removeBody(self.lastid)

        self.lastid = p.createMultiBody(baseMass = 1, baseVisualShapeIndex=self.visualid, baseCollisionShapeIndex=self.colisionid, basePosition=[startx, starty, 0.5])
        p.applyExternalForce(self.lastid, -1, [force_x,force_y,50], [0,0,0], p.LINK_FRAME)

        ball_xyz, _ = p.getBasePositionAndOrientation(self.lastid)

        self.robot.walk_target_x = ball_xyz[0]
        self.robot.walk_target_y = ball_xyz[1]

    def calc_rewards_and_done(self, a, state):
        if self.lastid:
            ball_xyz, _ = p.getBasePositionAndOrientation(self.lastid)
            self.robot.walk_target_x = ball_xyz[0]
            self.robot.walk_target_y = ball_xyz[1]
        done = self._termination(state)
        rewards = self._rewards(a)
        return rewards, done

    def _rewards(self, action = None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_potential()
        if self.flag_timeout > 225:
            progress = 0
        else:
            progress = float(self.potential - potential_old)

        if not a is None:
            electricity_cost = self.electricity_cost * float(np.abs(
                a * self.robot.joint_speeds).mean())  # let's assume we have DC motor with controller, and reverse current braking
            electricity_cost += self.stall_torque_cost * float(np.square(a).mean())
        else:
            electricity_cost = 0

        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        if alive == 0:
            alive_score = 0.1
        else:
            alive_score = -0.1

        joints_at_limit_cost = float(self.joints_at_limit_cost * self.robot.joints_at_limit)
        debugmode = 0
        if (debugmode):
            print("progress")
            print(progress)

        obstacle_penalty = 0

        #print("obs dist %.3f" %self.obstacle_dist)
        if self.obstacle_dist < 0.7:
            obstacle_penalty = self.obstacle_dist - 0.7

        rewards = [
            alive_score,
            progress,
            obstacle_penalty
        ]
        return rewards

    def _termination(self, state=None, debugmode=False):
        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        done = alive > 0 or self.nframe > 500
        if (debugmode):
            print("alive=")
            print(alive)
        if not np.isfinite(state).all():
            print("~INF~", state)
            done = True
        return done

    def _step(self, a):
        state, reward, done, meta = CameraRobotEnv._step(self, a)
        if self.flag_timeout <= 0 or (self.flag_timeout < 225 and self.robot.walk_target_dist < 0.8):
            self._flag_reposition()
        self.flag_timeout -= 1

        self.obstacle_dist = (np.mean(state[16:48,16:48,-1]))

        return state, reward, done, meta


class HuskyFetchKernelizedRewardEnv(CameraRobotEnv):
    """Specfy flagrun reward
    """
    def __init__(self, human=True, 
            timestep=HUSKY_TIMESTEP,
            frame_skip=HUSKY_FRAMESKIP, 
            is_discrete=False,
            gpu_count=0,
            mode="SENSOR",
            scene_type="building", 
            resolution="NORMAL"):
        self.human = human
        self.timestep = timestep
        self.frame_skip = frame_skip
        ## Mode initialized with mode=SENSOR
        self.model_id = configs.FETCH_MODEL_ID
        self.tracking_camera = tracking_camera
        
        target_orn, target_pos = configs.TASK_POSE[configs.FETCH_MODEL_ID]["fetch"][-1]
        initial_orn, initial_pos = configs.TASK_POSE[configs.FETCH_MODEL_ID]["fetch"][0]
        self.flag_timeout = 1

        CameraRobotEnv.__init__(
            self, 
            mode,
            gpu_count, 
            scene_type="building", 
            use_filler=False)
        self.robot_introduce(Husky(
            is_discrete=is_discrete, 
            initial_pos=initial_pos,
            initial_orn=initial_orn,
            resolution=resolution))
        self.scene_introduce()

        if self.human:
            self.visualid = p.createVisualShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.2, 0.2, 0.2], rgbaColor=[1, 0, 0, 0.7])
        self.colisionid = p.createCollisionShape(p.GEOM_MESH, fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'), meshScale=[0.2, 0.5, 0.2])

        self.lastid = None
        
    def _reset(self):
        obs = CameraRobotEnv._reset(self)
        return obs

    def _flag_reposition(self):
        #self.walk_target_x = self.np_random.uniform(low=-self.scene.stadium_halflen,
        #                                            high=+self.scene.stadium_halflen)
        #self.walk_target_y = self.np_random.uniform(low=-self.scene.stadium_halfwidth,
        #                                            high=+self.scene.stadium_halfwidth)
        force_x = self.np_random.uniform(-300,300)
        force_y = self.np_random.uniform(-300, 300)

        more_compact = 0.5  # set to 1.0 whole football field
        #self.walk_target_x *= more_compact
        #self.walk_target_y *= more_compact

        startx, starty, _ = self.robot.body_xyz


        self.flag = None
        #self.flag = self.scene.cpp_world.debug_sphere(self.walk_target_x, self.walk_target_y, 0.2, 0.2, 0xFF8080)
        self.flag_timeout = 600 / self.scene.frame_skip
        #print('targetxy', self.flagid, self.walk_target_x, self.walk_target_y, p.getBasePositionAndOrientation(self.flagid))
        #p.resetBasePositionAndOrientation(self.flagid, posObj = [self.walk_target_x, self.walk_target_y, 0.5], ornObj = [0,0,0,0])
        if self.lastid:
            p.removeBody(self.lastid)

        self.lastid = p.createMultiBody(baseMass = 1, baseVisualShapeIndex=self.visualid, baseCollisionShapeIndex=self.colisionid, basePosition=[startx, starty, 0.5])
        p.applyExternalForce(self.lastid, -1, [force_x,force_y,50], [0,0,0], p.LINK_FRAME)

        ball_xyz, _ = p.getBasePositionAndOrientation(self.lastid)

        self.robot.walk_target_x = ball_xyz[0]
        self.robot.walk_target_y = ball_xyz[1]

    def calc_rewards_and_done(self, a, state):
        if self.lastid:
            ball_xyz, _ = p.getBasePositionAndOrientation(self.lastid)
            self.robot.walk_target_x = ball_xyz[0]
            self.robot.walk_target_y = ball_xyz[1]
        done = self._termination(state)
        rewards = self._rewards(a)
        return rewards, done

    def _rewards(self, action=None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_potential()
        progress = float(self.potential - potential_old)

        if not a is None:
            electricity_cost = self.electricity_cost * float(np.abs(
                a * self.robot.joint_speeds).mean())  # let's assume we have DC motor with controller, and reverse current braking
            electricity_cost += self.stall_torque_cost * float(np.square(a).mean())
        else:
            electricity_cost = 0

        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        if alive == 0:
            alive_score = 0.1
        else:
            alive_score = -0.1

        joints_at_limit_cost = float(self.joints_at_limit_cost * self.robot.joints_at_limit)

        obstacle_penalty = 0
        if CALC_OBSTACLE_PENALTY and self._require_camera_input:
            obstacle_penalty=get_obstacle_penalty(self.robot, self.render_depth)

        debugmode = 0
        if (debugmode):
            print("alive=")
            print(alive)
            print("progress")
            print(progress)

        rewards = [
            alive_score,
            progress,
            obstacle_penalty
        ]
        return rewards

    def _termination(self, state=None, debugmode=False):
        alive = len(self.robot.parts['top_bumper_link'].contact_list())
        done = alive > 0 or self.nframe > 500
        if not np.isfinite(state).all():
            print("~INF~", state)
            done = True
        return done

    def _step(self, a):
        state, reward, done, meta = CameraRobotEnv._step(self, a)
        if self.flag_timeout <= 0:
            self._flag_reposition()
        self.flag_timeout -= 1
        return state, reward, done, meta


class HuskyGoallessRunEnv(CameraRobotEnv):
    """Specfy navigation reward
    """

    def __init__(
            self,
            human=True,
            timestep=HUSKY_TIMESTEP,
            frame_skip=HUSKY_FRAMESKIP,
            is_discrete=False,
            mode="RGBD",
            use_filler=True,
            gpu_count=0,
            resolution="NORMAL"):
        self.human = human
        self.model_id = configs.NAVIGATE_MODEL_ID
        self.timestep = timestep
        self.frame_skip = frame_skip
        self.resolution = resolution
        self.tracking_camera = tracking_camera
        target_orn, target_pos = configs.TASK_POSE[configs.NAVIGATE_MODEL_ID]["navigate"][-1]
        initial_orn, initial_pos = configs.TASK_POSE[configs.NAVIGATE_MODEL_ID]["navigate"][0]
        self.total_reward = 0
        self.total_frame = 0

        CameraRobotEnv.__init__(
            self,
            mode,
            gpu_count,
            scene_type="building",
            use_filler=use_filler)
        self.robot_introduce(Husky(
            is_discrete=is_discrete,
            initial_pos=initial_pos,
            initial_orn=initial_orn,
            target_pos=target_pos,
            resolution=resolution,
            mode=mode))
        self.scene_introduce()

    def calc_rewards_and_done(self, a, state):
        done = self._termination(state)
        rewards = self._rewards(a)
        debugmode = 0
        if debugmode:
            print("Frame %f reward %f" % (self.nframe, sum(rewards)))

        self.total_reward = self.total_reward + sum(rewards)
        self.total_frame = self.total_frame + 1
        # print(self.total_frame, self.total_reward)
        return rewards, done

    def _rewards(self, action=None, debugmode=False):
        a = action
        potential_old = self.potential
        self.potential = self.robot.calc_goalless_potential()
        progress = float(self.potential - potential_old)

        feet_collision_cost = 0.0
        for i, f in enumerate(
                self.robot.feet):  # TODO: Maybe calculating feet contacts could be done within the robot code
            # print(f.contact_list())
            contact_ids = set((x[2], x[4]) for x in f.contact_list())
            # print("CONTACT OF '%d' WITH %d" % (contact_ids, ",".join(contact_names)) )
            if (self.ground_ids & contact_ids):
                # see Issue 63: https://github.com/openai/roboschool/issues/63
                # feet_collision_cost += self.foot_collision_cost
                self.robot.feet_contact[i] = 1.0
            else:
                self.robot.feet_contact[i] = 0.0
        # print(self.robot.feet_contact)

        electricity_cost = self.electricity_cost * float(np.abs(a * self.robot.joint_speeds).mean())  # let's assume we
        electricity_cost += self.stall_torque_cost * float(np.square(a).mean())

        steering_cost = self.robot.steering_cost(a)

        obstacle_penalty = 0
        if CALC_OBSTACLE_PENALTY and self._require_camera_input:
            obstacle_penalty =get_obstacle_penalty(self.robot, self.render_depth)

        debugmode = 0
        if debugmode:
            print("steering cost", steering_cost)

        # alive = len(self.robot.parts['top_bumper_link'].contact_list())
        # if alive == 0:
        #    alive_score = 0.1
        # else:
        #    alive_score = -0.1

        wall_contact = [pt for pt in self.robot.parts['base_link'].contact_list() if pt[6][2] > 0.15]
        wall_collision_cost = self.wall_collision_cost * len(wall_contact)

        debugmode = 0
        if (debugmode):
            print("Wall contact points", len(wall_contact))
            print("Collision cost", wall_collision_cost)
            print("electricity_cost", electricity_cost)
            # print("progress")
            # print(progress)
            # print("electricity_cost")
            # print(electricity_cost)
            # print("joints_at_limit_cost")
            # print(joints_at_limit_cost)
            # print("feet_collision_cost")
            # print(feet_collision_cost)

        rewards = [
            progress,
            obstacle_penalty
            # wall_collision_cost,
            # electricity_cost,
            # joints_at_limit_cost,
            # feet_collision_cost
        ]
        return rewards

    def _termination(self, state=None, debugmode=False):
        alive = float(self.robot.alive_bonus(state[0] + self.robot.initial_z, self.robot.body_rpy[
            1]))  # state[0] is body height above ground, body_rpy[1] is pitch

        alive = len(self.robot.parts['top_bumper_link'].contact_list()) == 0

        done = not alive or self.nframe > 250 or self.robot.body_xyz[2] < 0
        # done = alive < 0
        if not np.isfinite(state).all():
            print("~INF~", state)
            done = True
        if done:
            print("Episode reset")

        return done

    def _flag_reposition(self):
        walk_target_x = self.robot.walk_target_x
        walk_target_y = self.robot.walk_target_y

        self.flag = None
        if self.human and not configs.DISPLAY_UI:
            self.visual_flagId = p.createVisualShape(p.GEOM_MESH,
                                                     fileName=os.path.join(pybullet_data.getDataPath(), 'cube.obj'),
                                                     meshScale=[0.5, 0.5, 0.5], rgbaColor=[1, 0, 0, 0.7])
            self.last_flagId = p.createMultiBody(baseVisualShapeIndex=self.visual_flagId, baseCollisionShapeIndex=-1,
                                                 basePosition=[walk_target_x, walk_target_y, 0.5])

    def _reset(self):
        self.total_frame = 0
        self.total_reward = 0
        obs = CameraRobotEnv._reset(self)
        self._flag_reposition()
        return obs


CALC_OBSTACLE_PENALTY = 1

def get_obstacle_penalty(robot, depth):
    screen_sz = robot.obs_dim[0]
    screen_delta = int(screen_sz / 8)
    screen_half  = int(screen_sz / 2)
    height_offset = int(screen_sz / 4)

    obstacle_dist = (np.mean(depth[screen_half  + height_offset - screen_delta : screen_half + height_offset + screen_delta, screen_half - screen_delta : screen_half + screen_delta, -1]))
    obstacle_penalty = 0
    OBSTACLE_LIMIT = 1.5
    if obstacle_dist < OBSTACLE_LIMIT:
       obstacle_penalty = (obstacle_dist - OBSTACLE_LIMIT)
    
    debugmode = 0
    if debugmode:
        #print("Obstacle screen", screen_sz, screen_delta)
        print("Obstacle distance", obstacle_dist)
        print("Obstacle penalty", obstacle_penalty)
    return obstacle_penalty