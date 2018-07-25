import gym
from gym import spaces
import numpy as np
# from os import path
import snakeoil3_gym as snakeoil3
import numpy as np
import copy
import collections as col
import os
import time
from matplotlib import pyplot as plt
import Helper
import math


class TorcsEnv:
    terminal_judge_start = 500  # Speed limit is applied after this step
    termination_limit_progress = 5  # [km/h], episode terminates if car is running slower than this limit
    default_speed = 50

    initial_reset = True
    file = open("ActionResults.txt", "w")

    def __init__(self, id, port, vision=False, throttle=False, gear_change=False):
        # print("Init")
        self.vision = vision
        self.id = id  # Provides a reference to the car.
        self.port = port  # Port on which the agent will connect to the Torcs server.
        self.throttle = throttle
        self.gear_change = gear_change

        self.initial_run = True

        ##print("launch torcs")
        # os.system('pkill torcs')
        time.sleep(0.5)
        if self.vision is True:
            os.system('torcs -nofuel -nodamage -nolaptime  -vision &')
        else:
            os.system('torcs  -nofuel -nodamage -nolaptime &')
        time.sleep(0.5)
        os.system('sh autostart.sh')
        time.sleep(0.5)

        # Modify here if you use multiple tracks in the environment
        self.client = snakeoil3.Client(p=self.port, vision=self.vision)  # Open new UDP in vtorcs

        self.client.MAX_STEPS = np.inf

        client = self.client

        # Client gets stuck here. Server does not respond because it is
        # waiting for other clients to connect.
        # client.get_servers_input()  # Get the initial input from torcs

        obs = client.ServerState.data  # Get the current full-observation from torcs
        self.obs = obs
        if throttle is False:
            self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(1,))
        else:
            self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(2,))

        if vision is False:
            high = np.array([1., np.inf, np.inf, np.inf, 1., np.inf, 1., np.inf])
            low = np.array([0., -np.inf, -np.inf, -np.inf, 0., -np.inf, 0., -np.inf])
            self.observation_space = spaces.Box(low=low, high=high)
        else:
            high = np.array([1., np.inf, np.inf, np.inf, 1., np.inf, 1., np.inf, 255])
            low = np.array([0., -np.inf, -np.inf, -np.inf, 0., -np.inf, 0., -np.inf, 0])
            self.observation_space = spaces.Box(low=low, high=high)

    def step(self, u):
        # print("Step")
        # convert thisAction to the actual torcs actionstr
        client = self.client

        this_action = self.agent_to_torcs(u)

        # Apply Action
        action_torcs = client.R.d

        # Steering
        action_torcs['steer'] = this_action['steer']  # in [-1, 1]

        #  Simple Autnmatic Throttle Control by Snakeoil
        if self.throttle is False:
            target_speed = self.default_speed
            if client.ServerState.data['speedX'] < target_speed - (client.R.d['steer'] * 50):
                client.R.d['accel'] += .01
            else:
                client.R.d['accel'] -= .01

            if client.R.d['accel'] > 0.2:
                client.R.d['accel'] = 0.2

            if client.ServerState.data['speedX'] < 10:
                client.R.d['accel'] += 1 / (client.ServerState.data['speedX'] + .1)

            # Traction Control System
            if ((client.ServerState.data['wheelSpinVel'][2] + client.ServerState.data['wheelSpinVel'][3]) -
                    (client.ServerState.data['wheelSpinVel'][0] + client.ServerState.data['wheelSpinVel'][1]) > 5):
                action_torcs['accel'] -= .2
        else:
            action_torcs['accel'] = this_action['accel']

        #  Automatic Gear Change by Snakeoil
        if self.gear_change is True:
            action_torcs['gear'] = this_action['gear']
        else:
            #  Automatic Gear Change by Snakeoil is possible
            action_torcs['gear'] = 1
            """
            if client.S.d['speedX'] > 50:
                action_torcs['gear'] = 2
            if client.S.d['speedX'] > 80:
                action_torcs['gear'] = 3
            if client.S.d['speedX'] > 110:
                action_torcs['gear'] = 4
            if client.S.d['speedX'] > 140:
                action_torcs['gear'] = 5
            if client.S.d['speedX'] > 170:
                action_torcs['gear'] = 6
            """

        # Save the privious full-obs from torcs for the reward calculation
        obs_pre = copy.deepcopy(client.ServerState.data)

        # One-Step Dynamics Update #################################
        # Apply the Agent's action into torcs
        client.respond_to_server()
        # Get the response of TORCS
        client.get_servers_input()

        # Get the current full-observation from torcs
        obs = client.ServerState.data

        # Make an obsevation from a raw observation vector from TORCS
        self.observation = self.make_observaton(obs)

        # Reward setting Here #######################################
        # direction-dependent positive reward
        track = np.array(obs['track'])
        sp = np.array(obs['speedX'])
        progress = sp * np.cos(obs['angle'])
        reward = progress

        # collision detection
        if obs['damage'] - obs_pre['damage'] > 0:
            reward = -1

        # Termination judgement #########################
        episode_terminate = False
        if track.min() < 0:  # Episode is terminated if the car is out of track
            reward = - 1
            episode_terminate = True
            client.R.d['meta'] = True

        if self.terminal_judge_start < self.time_step:  # Episode terminates if the progress of agent is small
            if progress < self.termination_limit_progress:
                episode_terminate = True
                client.R.d['meta'] = True

        if np.cos(obs['angle']) < 0:  # Episode is terminated if the agent runs backward
            episode_terminate = True
            client.R.d['meta'] = True

        if client.R.d['meta'] is True:  # Send a      reset signal
            self.initial_run = False
            client.respond_to_server()

        self.time_step += 1

        return self.get_obs(), reward, client.R.d['meta'], {}

    def reset(self, relaunch=False):
        # print("Reset")

        self.time_step = 0

        if not self.initial_reset:
            self.client.R.d['meta'] = True
            self.client.respond_to_server()

            ## TENTATIVE. Restarting TORCS every episode suffers the memory leak bug!
            if relaunch is True:
                self.reset_torcs()
                print("### TORCS is RELAUNCHED ###")

        # Modify here if you use multiple tracks in the environment
        self.client = snakeoil3.Client(p=self.port, vision=self.vision)  # Open new UDP in vtorcs
        self.client.MAX_STEPS = np.inf

        client = self.client
        client.get_servers_input()  # Get the initial input from torcs

        obs = client.ServerState.data  # Get the current full-observation from torcs
        self.observation = self.make_observaton(obs)

        self.last_u = None

        self.initial_reset = False
        return self.get_obs()

    def end(self):
        os.system('pkill torcs')

    def get_obs(self):
        return self.observation

    def reset_torcs(self):
        # print("relaunch torcs")
        os.system('pkill torcs')
        time.sleep(0.5)
        if self.vision is True:
            os.system('torcs -nofuel -nodamage -nolaptime -vision &')
        else:
            os.system('torcs -nofuel -nodamage -nolaptime &')
        time.sleep(0.5)
        os.system('sh autostart.sh')
        time.sleep(0.5)

    def agent_to_torcs(self, u):
        torcs_action = {'steer': u[0]}

        if self.throttle is True:  # throttle action is enabled
            torcs_action.update({'accel': u[1]})

        if self.gear_change is True:  # gear change action is enabled
            torcs_action.update({'gear': u[2]})

        return torcs_action

    def obs_vision_to_image_rgb(self, obs_image_vec):
        image_vec = obs_image_vec
        rgb = []
        temp = []
        # convert size 64x64x3 = 12288 to 64x64=4096 2-D list 
        # with rgb values grouped together.
        # Format similar to the observation in openai gym
        for i in range(0, 12286, 3):
            temp.append(image_vec[i])
            temp.append(image_vec[i + 1])
            temp.append(image_vec[i + 2])
            rgb.append(temp)
            temp = []
        return np.array(rgb, dtype=np.uint8)

    def make_observaton(self, raw_obs):
        if self.vision is False:
            names = ['focus',
                     'speedX', 'speedY', 'speedZ',
                     'opponents',
                     'rpm',
                     'track',
                     'wheelSpinVel']
            Observation = col.namedtuple('Observaion', names)
            return Observation(focus=np.array(raw_obs['focus'], dtype=np.float32) / 200.,
                               speedX=np.array(raw_obs['speedX'], dtype=np.float32) / self.default_speed,
                               speedY=np.array(raw_obs['speedY'], dtype=np.float32) / self.default_speed,
                               speedZ=np.array(raw_obs['speedZ'], dtype=np.float32) / self.default_speed,
                               opponents=np.array(raw_obs['opponents'], dtype=np.float32) / 200.,
                               rpm=np.array(raw_obs['rpm'], dtype=np.float32),
                               track=np.array(raw_obs['track'], dtype=np.float32) / 200.,
                               wheelSpinVel=np.array(raw_obs['wheelSpinVel'], dtype=np.float32))
        else:
            names = ['focus',
                     'speedX', 'speedY', 'speedZ',
                     'opponents',
                     'rpm',
                     'track',
                     'wheelSpinVel',
                     'img']
            Observation = col.namedtuple('Observaion', names)

            # Get RGB from observation
            image_rgb = self.obs_vision_to_image_rgb(raw_obs[names[8]])

            return Observation(focus=np.array(raw_obs['focus'], dtype=np.float32) / 200.,
                               speedX=np.array(raw_obs['speedX'], dtype=np.float32) / self.default_speed,
                               speedY=np.array(raw_obs['speedY'], dtype=np.float32) / self.default_speed,
                               speedZ=np.array(raw_obs['speedZ'], dtype=np.float32) / self.default_speed,
                               opponents=np.array(raw_obs['opponents'], dtype=np.float32) / 200.,
                               rpm=np.array(raw_obs['rpm'], dtype=np.float32),
                               track=np.array(raw_obs['track'], dtype=np.float32) / 200.,
                               wheelSpinVel=np.array(raw_obs['wheelSpinVel'], dtype=np.float32),
                               img=image_rgb)

    def new_episode(self):
        """Initiates a new episode."""
        self.reset(relaunch=False)  # TODO Will need to reset to overcome memory leak.

    def get_state(self):
        """Returns the current screen capture."""
        ob = self.make_observaton(self.client.ServerState.data)

        return ob.img

    def is_episode_finished(self):
        # Going backwards, crashed, time limit exceeded.
        # Or the server has been shut down by another car.
        """Returns a boolean value indicating whether an episode is finished or not."""

        if self.client.is_server_shutdown():
            return True

        self.client.get_servers_input()
        obs = self.client.ServerState.data
        track = np.array(obs['track'])
        sp = obs['speedX']
        progress = sp * np.cos(obs['angle'])

        off_track = track.min() < 0
        going_backwards = np.cos(obs['angle']) < 0
        not_making_progress = (
                    (self.terminal_judge_start < self.time_step) and (progress < self.termination_limit_progress))

        episodeFinished = off_track or going_backwards or not_making_progress

        self.file.write("Episode finished: " + str(episodeFinished) + "\n")
        self.file.write("Track: " + str(track) + "\n")

        return episodeFinished

    def make_action(self, action):
        """Executes an action in the environment, returns a reward."""
        oldObs = self.client.ServerState.data  # saving previous observation
        nextAction = self.agent_to_torcs(action)
        self.client.R.d['steer'] = nextAction['steer']

        self.client.respond_to_server()
        self.client.get_servers_input()
        newObs = self.client.ServerState.data
        self.observation = self.make_observaton(newObs)

        speed = newObs['speedX']
        angle = math.degrees(abs(newObs['angle']))
        # progress = (sp*np.cos(newObs['angle']))

        max_angle = 20

        print(self.id + ' ----------------------------------------')

        print('Angle %f' % angle)
        print('Speed %f' % speed)

        value = (max_angle - angle) if (max_angle - angle) > 0 else 0
        print('Angle value %f' % value)

        value = Helper.normalize(value, 0, max_angle)
        print('Normalized angle value %f' % value)

        value = value * value
        print('Normalized angle value squared %f' % value)

        value = Helper.denormalize(value, -10, 10)
        print('Inflated reward %f' % value)

        print('Action %f' % action)

        reward = value

        # self.file.write("Reward: "+str(reward)+"\n")
        # print("Reward: ", reward)
        return reward
