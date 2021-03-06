from __future__ import print_function
import random
import tensorflow as tf
import time
from tqdm import tqdm
import numpy as np

from dqn.agent import Agent
from dqn.environment import GymEnvironment, SimpleGymEnvironment
from config import get_config
from xitari_python_interface import ALEInterface, ale_fillRgbFromPalette
import pygame
from numpy.ctypeslib import as_ctypes
import sys
from dqn.game_screen import GameScreen
from dqn.scale import scale_image

flags = tf.compat.v1.flags

# Model
flags.DEFINE_string('model', 'm1', 'Type of model')
flags.DEFINE_boolean('dueling', False, 'Whether to use dueling deep q-network')
flags.DEFINE_boolean('double_q', False, 'Whether to use double q-learning')

# Environment
flags.DEFINE_string('env_name', 'Breakout-v0', 'The name of gym environment to use')
flags.DEFINE_integer('action_repeat', 4, 'The number of action to be repeated')

# Etc
flags.DEFINE_boolean('use_gpu', True, 'Whether to use gpu or not')
flags.DEFINE_string('gpu_fraction', '5/6', 'idx / # of gpu fraction e.g. 1/3, 2/3, 3/3')
flags.DEFINE_boolean('display', False, 'Whether to do display the game screen or not')
flags.DEFINE_boolean('is_train', True, 'Whether to do training or testing')
flags.DEFINE_integer('random_seed', 123, 'Value of random seed')
FLAGS = flags.FLAGS

# Set random seed

tf.random.set_seed(FLAGS.random_seed)
random.seed(FLAGS.random_seed)

if FLAGS.gpu_fraction == '':
  raise ValueError("--gpu_fraction should be defined")

def calc_gpu_fraction(fraction_string):
  idx, num = fraction_string.split('/')
  idx, num = float(idx), float(num)

  fraction = 1 / (num - idx + 1)
  print(" [*] GPU : %.4f" % fraction)
  return fraction

def getRgbFromPalette(ale, surface, rgb_new):
    # Environment parameters
    width = ale.ale_getScreenWidth()
    height = ale.ale_getScreenHeight()

    # Get current observations
    obs = np.zeros(width * height, dtype=np.uint8)
    n_obs = obs.shape[0]
    ale.ale_fillObs(as_ctypes(obs), width * height)

    # Get RGB values of values
    n_rgb = n_obs * 3
    rgb = np.zeros(n_rgb, dtype=np.uint8)
    ale_fillRgbFromPalette(as_ctypes(rgb), as_ctypes(obs), n_rgb, n_obs)

    # Convert uint8 array into uint32 array for pygame visualization
    for i in range(n_obs):
        # Convert current pixel into RGBA format in pygame
        cur_color = pygame.Color(int(rgb[i]), int(rgb[i + n_obs]), int(rgb[i + 2 * n_obs]))
        cur_mapped_int = surface.map_rgb(cur_color)
        rgb_new[i] = cur_mapped_int

    # Reshape and roll axis until it fits imshow dimensions
    return np.rollaxis(rgb.reshape(3, height, width), axis=0, start=3)


def main(_):
  with tf.compat.v1.Session() as sess:
    config = get_config(FLAGS) or FLAGS

    if config.env_type == 'simple':
      env = SimpleGymEnvironment(config)
    else:
      env = GymEnvironment(config)

    print("tf.test.is_gpu_available(): {}".format(tf.test.is_gpu_available()))

    if not tf.test.is_gpu_available() and FLAGS.use_gpu:
      raise Exception("use_gpu flag is true when no GPUs are available")

    if not FLAGS.use_gpu:
      config.cnn_format = 'NHWC'

    roms = 'roms/Pong2PlayerVS.bin'
    ale = ALEInterface(roms.encode('utf-8'))
    width = ale.ale_getScreenWidth()
    height = ale.ale_getScreenHeight()
    game_screen = GameScreen()
    ale.ale_resetGame()
    (display_width, display_height) = (width * 2, height * 2)

    pygame.init()
    screen_ale = pygame.display.set_mode((display_width, display_height))
    pygame.display.set_caption("Arcade Learning Environment Random Agent Display")
    pygame.display.flip()

    game_surface = pygame.Surface((width, height), depth=8)
    clock = pygame.time.Clock()

    # Clear screen
    screen_ale.fill((0, 0, 0))
    agent = Agent(config, env, sess, 'A')


    if FLAGS.is_train:
      start_step = agent.step_op.eval()
      start_time = time.time()

      num_game, agent.update_count, ep_reward = 0, 0, 0.
      total_reward, agent.total_loss, agent.total_q = 0., 0., 0.
      max_avg_ep_reward = 0
      ep_rewards, actions = [], []

      numpy_surface = np.frombuffer(game_surface.get_buffer(), dtype=np.uint8)
      rgb = getRgbFromPalette(ale, game_surface, numpy_surface)
      del numpy_surface
      game_screen.paint(rgb)
      pooled_screen = game_screen.grab()
      scaled_pooled_screen = scale_image(pooled_screen)

      for _ in range(agent.history_length):
        agent.history.add(scaled_pooled_screen)

      for agent.step in tqdm(range(start_step, agent.max_step), ncols=70, initial=start_step):

        if agent.step == agent.learn_start:
          num_game, agent.update_count, ep_reward = 0, 0, 0.
          total_reward, agent.total_loss, agent.total_q = 0., 0., 0.
          ep_rewards, actions = [], []

        # 1. predict
        if config.cnn_format == 'NHWC':
            action = agent.predict(agent.history.get().T)
        else:
            action = agent.predict(agent.history.get())


        # 2. act
        ale.ale_act2(action, np.random.choice([20, 21, 23, 24]))
        terminal = ale.ale_isGameOver()
        reward = ale.ale_getRewardA()


        # screen, reward, terminal = agent.env.act(action, is_training=True)
        # 3. observe
        # Both agents perform random actions
        # Agent A : [NOOP, FIRE, RIGHT, LEFT]
        # Agent B : [NOOP, FIRE, RIGHT, LEFT]

        # Fill buffer of game screen with current frame
        numpy_surface = np.frombuffer(game_surface.get_buffer(), dtype=np.uint8)
        rgb = getRgbFromPalette(ale, game_surface, numpy_surface)
        del numpy_surface
        game_screen.paint(rgb)
        pooled_screen = game_screen.grab()
        scaled_pooled_screen = scale_image(pooled_screen)
        agent.observe(scaled_pooled_screen, reward, action, terminal)



        # Print frame onto display screen
        screen_ale.blit(pygame.transform.scale2x(game_surface), (0, 0))

        #Update the display screen
        pygame.display.flip()

        if terminal:
          ale.ale_resetGame()
          terminal = ale.ale_isGameOver()
          reward = ale.ale_getRewardA()
          numpy_surface = np.frombuffer(game_surface.get_buffer(), dtype=np.uint8)

          rgb = getRgbFromPalette(ale, game_surface, numpy_surface)
          del numpy_surface
          game_screen.paint(rgb)
          pooled_screen = game_screen.grab()
          scaled_pooled_screen = scale_image(pooled_screen)

          num_game += 1
          ep_rewards.append(ep_reward)
          ep_reward = 0.
        else:
          ep_reward += reward

        actions.append(action)
        total_reward += reward

        if agent.step >= agent.learn_start:
          if agent.step % agent.test_step == agent.test_step - 1:
            avg_reward = total_reward / agent.test_step
            avg_loss = agent.total_loss / agent.update_count
            avg_q = agent.total_q / agent.update_count

            try:
              max_ep_reward = np.max(ep_rewards)
              min_ep_reward = np.min(ep_rewards)
              avg_ep_reward = np.mean(ep_rewards)
            except:
              max_ep_reward, min_ep_reward, avg_ep_reward = 0, 0, 0

            print('\navg_r: %.4f, avg_l: %.6f, avg_q: %3.6f, avg_ep_r: %.4f, max_ep_r: %.4f, min_ep_r: %.4f, # game: %d' \
                % (avg_reward, avg_loss, avg_q, avg_ep_reward, max_ep_reward, min_ep_reward, num_game))

            if max_avg_ep_reward * 0.9 <= avg_ep_reward:
              agent.step_assign_op.eval({agent.step_input: agent.step + 1})
              agent.save_model(agent.step + 1)

              max_avg_ep_reward = max(max_avg_ep_reward, avg_ep_reward)

            if agent.step > 180:
              agent.inject_summary({
                  'average.reward': avg_reward,
                  'average.loss': avg_loss,
                  'average.q': avg_q,
                  'episode.max reward': max_ep_reward,
                  'episode.min reward': min_ep_reward,
                  'episode.avg reward': avg_ep_reward,
                  'episode.num of game': num_game,
                  'episode.rewards': ep_rewards,
                  'episode.actions': actions,
                  'training.learning_rate': agent.learning_rate_op.eval({agent.learning_rate_step: agent.step}),
                }, agent.step)

            num_game = 0
            total_reward = 0.
            agent.total_loss = 0.
            agent.total_q = 0.
            agent.update_count = 0
            ep_reward = 0.
            ep_rewards = []
            actions = []
    else:
      while not ale.ale_isGameOver():

    # Fill buffer of game screen with current frame
        numpy_surface = np.frombuffer(game_surface.get_buffer(), dtype=np.uint8)
        rgb = getRgbFromPalette(ale, game_surface, numpy_surface)
        del numpy_surface
        game_screen.paint(rgb)
        pooled_screen = game_screen.grab()
        scaled_pooled_screen = scale_image(pooled_screen)

        ale.ale_act2(agent.predict(pooled_screen), np.random.choice([20, 21, 23, 24]))

        print(ale.ale_getRewardA())
    # Print frame onto display screen
        screen.blit(pygame.transform.scale2x(game_surface), (0, 0))

    # Update the display screen
        pygame.display.flip()

    # delay to 60fps
        clock.tick(60.)


if __name__ == '__main__':
    tf.compat.v1.app.run(
        main=None, argv=None
    )
    print("thats why")
