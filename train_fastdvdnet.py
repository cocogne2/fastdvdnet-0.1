"""
Trains a FastDVDnet model.

Copyright (C) 2019, Matias Tassano <matias.tassano@parisdescartes.fr>

This program is free software: you can use, modify and/or
redistribute it under the terms of the GNU General Public
License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later
version. You should have received a copy of this license along
this program. If not, see <http://www.gnu.org/licenses/>.
"""
import time
import argparse
import torch
import torch.nn as nn
import torch.optim as optim
from models import FastDVDnet
from dataset import ValDataset
from dataloaders import train_dali_loader
from utils import svd_orthogonalization, close_logger, init_logging, normalize_augment
from train_common import resume_training, lr_scheduler, log_train_psnr, \
					validate_and_log, save_model_checkpoint

import numpy as np
import matplotlib.pyplot as plt
import sys
from skimage.util import random_noise


def main(**args):
	r"""Performs the main training loop
	"""

	# Load dataset
	print('> Loading datasets ...')
	dataset_val = ValDataset(valsetdir=args['valset_dir'], gray_mode=False)
	loader_train = train_dali_loader(batch_size=args['batch_size'],\
									file_root=args['trainset_dir'],\
									sequence_length=args['temp_patch_size'],\
									crop_size=args['patch_size'],\
									epoch_size=args['max_number_patches'],\
									random_shuffle=True,\
									temp_stride=3)

	num_minibatches = int(args['max_number_patches']//args['batch_size'])
	ctrl_fr_idx = (args['temp_patch_size'] - 1) // 2
	print("\t# of training samples: %d\n" % int(args['max_number_patches']))

	# Init loggers
	writer, logger = init_logging(args)

	# Define GPU devices
	device_ids = [0]
	torch.backends.cudnn.benchmark = True # CUDNN optimization

	# Create model
	model = FastDVDnet()
	model = nn.DataParallel(model, device_ids=device_ids).cuda()

	# Define loss
	criterion = nn.MSELoss(reduction='sum')
	criterion.cuda()

	# Optimizer
	optimizer = optim.Adam(model.parameters(), lr=args['lr'])

	# Resume training or start anew
	start_epoch, training_params = resume_training(args, model, optimizer)

	# Training
	start_time = time.time()
	for epoch in range(start_epoch, args['epochs']):
		# Set learning rate
		current_lr, reset_orthog = lr_scheduler(epoch, args)
		if reset_orthog:
			training_params['no_orthog'] = True

		# set learning rate in optimizer
		for param_group in optimizer.param_groups:
			param_group["lr"] = current_lr
		print('\nlearning rate %f' % current_lr)

		# train

		for i, data in enumerate(loader_train, 0):

			# Pre-training step
			model.train()

			# When optimizer = optim.Optimizer(net.parameters()) we only zero the optim's grads
			optimizer.zero_grad()

			# convert inp to [N, num_frames*C. H, W] in  [0., 1.] from [N, num_frames, C. H, W] in [0., 255.]
			# extract ground truth (central frame)
			img_train, gt_train = normalize_augment(data[0]['data'], ctrl_fr_idx)
			
#			plt.imshow(gt_train[1,:,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
#			plt.savefig("/content/gdrive/My Drive/projet_7/savefig0.png")
#			plt.imshow(img_train[1,0:3,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
#			plt.savefig("/content/gdrive/My Drive/projet_7/savefig1.png")
#			plt.imshow(img_train[1,3:6,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
#			plt.savefig("/content/gdrive/My Drive/projet_7/savefig1bis.png")
#			plt.imshow(img_train[1,6:9,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
#			plt.savefig("/content/gdrive/My Drive/projet_7/savefig1ter.png")
#			plt.show()
			N, L, H, W = img_train.size()
			
			if args['type_noise']=="gaussian":
                # std dev of each sequence
                                    stdn = torch.empty((N, 1, 1, 1)).cuda().uniform_(args['noise_ival'][0], to=args['noise_ival'][1])
                # draw noise samples from std dev tensor
                                    noise = torch.zeros_like(img_train)
                                    noise = torch.normal(mean=noise, std=stdn.expand_as(noise))
                #define noisy input
                                    imgn_train = img_train + noise
#                                    plt.imshow(imgn_train[1,0:3,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
#                                    plt.savefig("/content/gdrive/My Drive/projet_7/savefig1_gauss.png")
#                                    sys.exit()
                                    
			if args['type_noise']=="uniform":
# std dev of each sequence
                                    stdn = torch.empty((N, 1, 1, 1)).cuda().uniform_(args['noise_ival'][0], to=args['noise_ival'][1])
# draw noise samples from std dev tensor
                                    v_max=np.sqrt(3)*stdn
                                    noise = torch.empty((N,L,H,W)).cuda().uniform(-1,to=1)
                                    # Pytorch accept? 
                                    noise = noise*stdn.expand_as(noise)
                                    noise2 = torch.empty((N,L,H,W)).cuda().uniform(-1,to=1)*stdn.expand_as(noise)*np.sqrt(3).cuda()
                                    for img_du_batch in range(N):
                                        noise2[img_du_batch,:,:,:] = noise[img_du_batch,:,:,:]*v_max[img_du_batch,1,1,1]
                                    print(noise-noise2)

			if args['type_noise']=="poisson":
                                    peak=args['poisson_peak']
                                    imgn_train = torch.poisson(img_train /255 * peak ) / float(peak) *255
                                    noise=imgn_train-img_train
                                    std=torch.std(noise,unbiased=True)
                                    mean=torch.mean(noise)
                                    stdn = torch.empty((N, 1, 1, 1)).cuda().normal_(mean=mean,std=std)
                                    plt.imshow(imgn_train[1,0:3,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
                                    plt.savefig("/content/gdrive/My Drive/projet_7/savefig1_poisson.png")
                                    sys.exit()
			if args['type_noise']=="s&p":
                                    s_vs_p = 0.5
                                    # Salt mode
                                    imgn_train = torch.tensor(random_noise(img_train.cpu(), mode='s&p', salt_vs_pepper=s_vs_p, clip=True)).cuda()
                                    noise=imgn_train-img_train
                                    stdn = torch.empty((N, 1, 1, 1)).cuda()
                                    for img_du_batch in range(N):
                                        stdn[img_du_batch,:,:,:]=torch.std(noise[img_du_batch,:,:,:],unbiased=True) 
#                                    plt.imshow(imgn_train[1,0:3,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
#                                    plt.savefig("/content/gdrive/My Drive/projet_7/savefig1_s&p.png")
#                                    sys.exit()
                                    
			if args['type_noise']=="speckle":
                                    varia=args['speckle_var']
                                    imgn_train = torch.tensor(random_noise(img_train.cpu(), mode='speckle', mean=0, var=varia, clip=True)).cuda().float()
                                    noise=imgn_train-img_train
                                    stdn = torch.empty((N, 1, 1, 1)).cuda()
                                    for img_du_batch in range(N):
                                        stdn[img_du_batch,:,:,:]=torch.std(noise[img_du_batch,:,:,:],unbiased=True) 
#                                    plt.imshow(imgn_train[1,0:3,:,:].unsqueeze(0).cuda().detach().cpu().clone().numpy().swapaxes(0,3).swapaxes(1,2).squeeze())
#                                    plt.savefig("/content/gdrive/My Drive/projet_7/savefig1_speckle.png")
#                                    sys.exit()                        
			# Send tensors to GPU
			gt_train = gt_train.cuda(non_blocking=True)
			imgn_train = imgn_train.cuda(non_blocking=True)
			noise = noise.cuda(non_blocking=True)
			noise_map = stdn.expand((N, 1, H, W)).cuda(non_blocking=True) # one channel per image

			# Evaluate model and optimize it
			out_train = model(imgn_train, noise_map)

			# Compute loss
			loss = criterion(gt_train, out_train) / (N*2)
			loss.backward()
			optimizer.step()

			# Results
			if training_params['step'] % args['save_every'] == 0:
				# Apply regularization by orthogonalizing filters
				if not training_params['no_orthog']:
					model.apply(svd_orthogonalization)

				# Compute training PSNR
				log_train_psnr(out_train, \
								gt_train, \
								loss, \
								writer, \
								epoch, \
								i, \
								num_minibatches, \
								training_params)
			# update step counter
			training_params['step'] += 1

		# Call to model.eval() to correctly set the BN layers before inference
		model.eval()

		# Validation and log images
		print("model:",model)
		print("dataset:",dataset_val)
		print("writer:",writer)
		print("epoch:",epoch)
		print("current_lr:",current_lr)
		print("logger:",logger)
		validate_and_log(
						model_temp=model, \
						dataset_val=dataset_val, \
						valnoisestd=args['val_noiseL'], \
						temp_psz=args['temp_patch_size'], \
						writer=writer, \
						epoch=epoch, \
						lr=current_lr, \
						logger=logger, \
						trainimg=img_train
						)

		# save model and checkpoint
		training_params['start_epoch'] = epoch + 1
		save_model_checkpoint(model, args, optimizer, training_params, epoch)

	# Print elapsed time
	elapsed_time = time.time() - start_time
	print('Elapsed time {}'.format(time.strftime("%H:%M:%S", time.gmtime(elapsed_time))))

	# Close logger file
	close_logger(logger)

if __name__ == "__main__":

	parser = argparse.ArgumentParser(description="Train the denoiser")

	#Training parameters
	parser.add_argument("--batch_size", type=int, default=64, 	\
					 help="Training batch size")
	parser.add_argument("--epochs", "--e", type=int, default=80, \
					 help="Number of total training epochs")
	parser.add_argument("--resume_training", "--r", action='store_true',\
						help="resume training from a previous checkpoint")
	parser.add_argument("--milestone", nargs=2, type=int, default=[50, 60], \
						help="When to decay learning rate; should be lower than 'epochs'")
	parser.add_argument("--lr", type=float, default=1e-3, \
					 help="Initial learning rate")
	parser.add_argument("--no_orthog", action='store_true',\
						help="Don't perform orthogonalization as regularization")
	parser.add_argument("--save_every", type=int, default=10,\
						help="Number of training steps to log psnr and perform \
						orthogonalization")
	parser.add_argument("--save_every_epochs", type=int, default=5,\
						help="Number of training epochs to save state")
###########################
#  AJOUT                  #	
###########################
	parser.add_argument("--type_noise", type=str, default="gaussian", \
                        help="type of the noise:gaussian,s&p,poisson")
	parser.add_argument("--poisson_peak", type=float, default=25.0, \
                        help="peak of the poisson noise")
	parser.add_argument("--speckle_var", type=float, default=0.05, \
                        help="variance of the speckle function")
###########################
	parser.add_argument("--noise_ival", nargs=2, type=int, default=[5, 55], \
					 help="Noise training interval")
	parser.add_argument("--val_noiseL", type=float, default=25, \
						help='noise level used on validation set')
	# Preprocessing parameters
	parser.add_argument("--patch_size", "--p", type=int, default=96, help="Patch size")
	parser.add_argument("--temp_patch_size", "--tp", type=int, default=5, help="Temporal patch size")
	parser.add_argument("--max_number_patches", "--m", type=int, default=256000, \
						help="Maximum number of patches")
	# Dirs
	parser.add_argument("--log_dir", type=str, default="logs", \
					 help='path of log files')
	parser.add_argument("--trainset_dir", type=str, default=None, \
					 help='path of trainset')
	parser.add_argument("--valset_dir", type=str, default=None, \
						 help='path of validation set')
	argspar = parser.parse_args()

	# Normalize noise between [0, 1]
	argspar.val_noiseL /= 255.
	argspar.noise_ival[0] /= 255.
	argspar.noise_ival[1] /= 255.

	print("\n### Training FastDVDnet denoiser model ###")
	print("> Parameters:")
	for p, v in zip(argspar.__dict__.keys(), argspar.__dict__.values()):
		print('\t{}: {}'.format(p, v))
	print('\n')

	main(**vars(argspar))
