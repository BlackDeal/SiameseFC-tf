import tensorflow as tf
import numpy as np
import scipy.io
import sys
import os.path
from src.convolutional import set_convolutional, set_convolutional_train
from src.crops import extract_crops_z, extract_crops_x, pad_frame, resize_images
sys.path.append('../')

pos_x_ph = tf.placeholder(tf.float64)
pos_y_ph = tf.placeholder(tf.float64)
z_sz_ph = tf.placeholder(tf.float64)
x_sz0_ph = tf.placeholder(tf.float64)
x_sz1_ph = tf.placeholder(tf.float64)
x_sz2_ph = tf.placeholder(tf.float64)

x_pos_x = tf.placeholder(tf.float64)
x_pos_y = tf.placeholder(tf.float64)
x_target_w = tf.placeholder(tf.float64)
x_target_h = tf.placeholder(tf.float64)

batch_size = 5
batched_pos_x_ph = tf.placeholder(tf.float64, shape = [batch_size])
batched_pos_y_ph = tf.placeholder(tf.float64, shape = [batch_size])
batched_z_sz_ph = tf.placeholder(tf.float64, shape = [batch_size])
batched_x_sz0_ph = tf.placeholder(tf.float64, shape = [batch_size])
batched_x_sz1_ph = tf.placeholder(tf.float64, shape = [batch_size])
batched_x_sz2_ph = tf.placeholder(tf.float64, shape = [batch_size])

batched_x_pos_x = tf.placeholder(tf.float64, shape = [batch_size])
batched_x_pos_y = tf.placeholder(tf.float64, shape = [batch_size])
batched_x_target_w = tf.placeholder(tf.float64, shape = [batch_size])
batched_x_target_h = tf.placeholder(tf.float64, shape = [batch_size])


label = tf.placeholder(tf.float32, [batch_size, None, None])
# the follow parameters *have to* reflect the design of the network to be imported
_conv_stride = np.array([2,1,1,1,1])
_filtergroup_yn = np.array([0,1,0,1,1], dtype=bool)
_bnorm_yn = np.array([1,1,1,1,0], dtype=bool)
_relu_yn = np.array([1,1,1,1,0], dtype=bool)
_pool_stride = np.array([2,1,0,0,0]) # 0 means no pool
_pool_sz = 3
_bnorm_adjust = True
assert len(_conv_stride) == len(_filtergroup_yn) == len(_bnorm_yn) == len(_relu_yn) == len(_pool_stride), ('These arrays of flags should have same length')
assert all(_conv_stride) >= True, ('The number of conv layers is assumed to define the depth of the network')
_num_layers = len(_conv_stride)


def build_tracking_graph(final_score_sz, design, env):
    # Make a queue of file names
    # filename_queue = tf.train.string_input_producer(frame_name_list, shuffle=False, capacity=num_frames)
    # image_reader = tf.WholeFileReader()
    # # Read a whole file from the queue
    # image_name, image_file = image_reader.read(filename_queue)

    filename = tf.placeholder(tf.string, [], name='filename')
    image_file = tf.read_file(filename)
    # Decode the image as a JPEG file, this will turn it into a Tensor
    image = tf.image.decode_jpeg(image_file)
    image = 255.0 * tf.image.convert_image_dtype(image, tf.float32)
    frame_sz = tf.shape(image)
    # used to pad the crops
    if design.pad_with_image_mean:
        avg_chan = tf.reduce_mean(image, axis=(0,1), name='avg_chan')
    else:
        avg_chan = None
    # pad with if necessary


    frame_padded_z, npad_z = pad_frame(image, frame_sz, pos_x_ph, pos_y_ph, z_sz_ph, avg_chan)
    frame_padded_z = tf.cast(frame_padded_z, tf.float32)
    # extract tensor of z_crops
    z_crops = extract_crops_z(frame_padded_z, npad_z, pos_x_ph, pos_y_ph, z_sz_ph, design.exemplar_sz)

    frame_padded_x, npad_x = pad_frame(image, frame_sz, pos_x_ph, pos_y_ph, x_sz2_ph, avg_chan)
    frame_padded_x = tf.cast(frame_padded_x, tf.float32)
    # extract tensor of x_crops (3 scales)
    x_crops = extract_crops_x(frame_padded_x, npad_x, pos_x_ph, pos_y_ph, x_sz0_ph, x_sz1_ph, x_sz2_ph, design.search_sz)
    print("shape of x_crops: ", x_crops.shape)
    # use crops as input of (MatConvnet imported) pre-trained fully-convolutional Siamese net
    template_z, templates_x, p_names_list, p_val_list = _create_siamese(os.path.join(env.root_pretrained,design.net), x_crops, z_crops)
    print("shape of z:", template_z.shape)
    print("shape of x:", templates_x.shape)
    template_z = tf.squeeze(template_z)
    templates_z = tf.stack([template_z, template_z, template_z])
    print("shape of zssss:", templates_z.get_shape().as_list())
    # compare templates via cross-correlation
    scores = _match_templates(templates_z, templates_x, p_names_list, p_val_list)
    # upsample the score maps
    scores_up = tf.image.resize_images(scores, [final_score_sz, final_score_sz],
        method=tf.image.ResizeMethod.BICUBIC, align_corners=True)
    return filename, image, templates_z, scores_up


def build_tracking_graph_train(final_score_sz, design, env, hp, batch_size, resize_width, resize_height, channel, input_batch_size):
    # Make a queue of file names
    # filename_queue = tf.train.string_input_producer(frame_name_list, shuffle=False, capacity=num_frames)
    # image_reader = tf.WholeFileReader()
    # # Read a whole file from the queue
    # image_name, image_file = image_reader.read(filename_queue)
    batch_size = input_batch_size
    image = tf.placeholder(tf.float32, [batch_size, resize_width, resize_height, channel], name = "input_image")
    print("image size: ", image.shape)
    frame_sz = [resize_width, resize_height, channel]
    
    # used to pad the crops
    if design.pad_with_image_mean:
        avg_chan = tf.reduce_mean(image, axis=(0,1, 2), name='avg_chan') ####need to change to the mean value of each img##########
    else:
        avg_chan = None
    # pad with if necessary
    single_crops_z = []
    single_crops_x = []
    for batch in range(batch_size):
        single_z = tf.squeeze(tf.gather(image, [batch]))

        single_pos_x_ph = tf.squeeze(tf.gather(batched_pos_x_ph, [batch]))
        single_pos_y_ph = tf.squeeze(tf.gather(batched_pos_y_ph, [batch]))
        single_z_sz_ph = tf.squeeze(tf.gather(batched_z_sz_ph, [batch]))

        single_x_sz0_ph = tf.squeeze(tf.gather(batched_x_sz0_ph, [batch]))
        single_x_sz1_ph = tf.squeeze(tf.gather(batched_x_sz1_ph, [batch]))
        single_x_sz2_ph = tf.squeeze(tf.gather(batched_x_sz2_ph, [batch]))

        frame_padded_z, npad_z = pad_frame(single_z, frame_sz, single_pos_x_ph, single_pos_y_ph, single_z_sz_ph, avg_chan)
        frame_padded_z = tf.cast(frame_padded_z, tf.float32)
        # extract tensor of z_crops
        single_crops_z.append(tf.squeeze(extract_crops_z(frame_padded_z, npad_z, single_pos_x_ph, single_pos_y_ph, single_z_sz_ph, design.exemplar_sz)))
    
        single_x = tf.gather(image, [batch])
        single_x = tf.squeeze(single_x)

        frame_padded_x, npad_x = pad_frame(single_x, frame_sz, single_pos_x_ph, single_pos_y_ph, single_x_sz2_ph, avg_chan)
        frame_padded_x = tf.cast(frame_padded_x, tf.float32)
        # extract tensor of x_crops (3 scales)
        single_crops_x.append(tf.squeeze(extract_crops_x(frame_padded_x, npad_x, single_pos_x_ph, single_pos_y_ph, single_x_sz0_ph, single_x_sz1_ph, single_x_sz2_ph, design.search_sz)))
    z_crops = tf.stack(single_crops_z)
    x_crops = tf.stack(single_crops_x)
    x_crops_shape = x_crops.get_shape().as_list()
    x_crops = tf.reshape(x_crops, [x_crops_shape[0] * x_crops_shape[1]] + x_crops_shape[2: ])
    print(single_crops_x[0].shape, x_crops.shape)
    # use crops as input of (MatConvnet imported) pre-trained fully-convolutional Siamese net
    template_z, templates_x = _create_siamese_train(os.path.join(env.root_pretrained,design.net), x_crops, z_crops, design)
    print("shape of z:", template_z.shape)
    print("shape of x:", templates_x.shape)
    template_z = tf.squeeze(template_z)
    templates_z = tf.stack([template_z, template_z, template_z])
    templates_z_shape = templates_z.get_shape().as_list()
    templates_z = tf.reshape(templates_z, [templates_z_shape[0] * templates_z_shape[1]] + templates_z_shape[2: ])
    print("shape of zssss:", templates_z.get_shape().as_list())
    print("shape of xssss:", templates_x.get_shape().as_list())
    # compare templates via cross-correlation
    scores = _match_templates_train(templates_z, templates_x)
    print("shape of small score map:", scores.get_shape().as_list())
    # upsample the score maps
    scores_up = tf.image.resize_images(scores, [final_score_sz, final_score_sz],
        method=tf.image.ResizeMethod.BICUBIC, align_corners=True)
    loss = cal_loss(scores, batch_size)
    train_step = tf.train.AdamOptimizer(hp.lr).minimize(loss)
    return image, templates_z, scores_up, loss, train_step

def cal_loss(scores, batch_size):
    #select the first row in scores as score
    idx = tf.constant([0 + 3 * i for i in range(batch_size)])
    score = tf.gather(scores, idx)
    score = tf.squeeze(score)
    
    loss = tf.reduce_mean(tf.log(1 + tf.exp(-score * label)))
    
    return loss




# import pretrained Siamese network from matconvnet
def _create_siamese(net_path, net_x, net_z):
    # read mat file from net_path and start TF Siamese graph from placeholders X and Z
    params_names_list, params_values_list = _import_from_matconvnet(net_path)

    # loop through the flag arrays and re-construct network, reading parameters of conv and bnorm layers
    for i in range(_num_layers):
        print('> Layer '+str(i+1))
        # conv
        conv_W_name = _find_params('conv'+str(i+1)+'f', params_names_list)[0]
        conv_b_name = _find_params('conv'+str(i+1)+'b', params_names_list)[0]
        print('\t\tCONV: setting '+conv_W_name+' '+conv_b_name)
        print('\t\tCONV: stride '+str(_conv_stride[i])+', filter-group '+str(_filtergroup_yn[i]))
        conv_W = params_values_list[params_names_list.index(conv_W_name)]
        conv_b = params_values_list[params_names_list.index(conv_b_name)]
        # batchnorm
        if _bnorm_yn[i]:
            bn_beta_name = _find_params('bn'+str(i+1)+'b', params_names_list)[0]
            bn_gamma_name = _find_params('bn'+str(i+1)+'m', params_names_list)[0]
            bn_moments_name = _find_params('bn'+str(i+1)+'x', params_names_list)[0]
            print('\t\tBNORM: setting '+bn_beta_name+' '+bn_gamma_name+' '+bn_moments_name)
            bn_beta = params_values_list[params_names_list.index(bn_beta_name)]
            bn_gamma = params_values_list[params_names_list.index(bn_gamma_name)]
            bn_moments = params_values_list[params_names_list.index(bn_moments_name)]
            bn_moving_mean = bn_moments[:,0]
            bn_moving_variance = bn_moments[:,1]**2 # saved as std in matconvnet
        else:
            bn_beta = bn_gamma = bn_moving_mean = bn_moving_variance = []
        
        # set up conv "block" with bnorm and activation 
        net_x = set_convolutional(net_x, conv_W, np.swapaxes(conv_b,0,1), _conv_stride[i], \
                            bn_beta, bn_gamma, bn_moving_mean, bn_moving_variance, \
                            filtergroup=_filtergroup_yn[i], batchnorm=_bnorm_yn[i], activation=_relu_yn[i], \
                            scope='conv'+str(i+1), reuse=False)
        
        # notice reuse=True for Siamese parameters sharing
        net_z = set_convolutional(net_z, conv_W, np.swapaxes(conv_b,0,1), _conv_stride[i], \
                            bn_beta, bn_gamma, bn_moving_mean, bn_moving_variance, \
                            filtergroup=_filtergroup_yn[i], batchnorm=_bnorm_yn[i], activation=_relu_yn[i], \
                            scope='conv'+str(i+1), reuse=True)    
        
        # add max pool if required
        if _pool_stride[i]>0:
            print('\t\tMAX-POOL: size '+str(_pool_sz)+ ' and stride '+str(_pool_stride[i]))
            net_x = tf.nn.max_pool(net_x, [1,_pool_sz,_pool_sz,1], strides=[1,_pool_stride[i],_pool_stride[i],1], padding='VALID', name='pool'+str(i+1))
            net_z = tf.nn.max_pool(net_z, [1,_pool_sz,_pool_sz,1], strides=[1,_pool_stride[i],_pool_stride[i],1], padding='VALID', name='pool'+str(i+1))

    

    return net_z, net_x, params_names_list, params_values_list

def _create_siamese_train(net_path, net_x, net_z, design):
    filter_h = design.filter_h
    filter_w = design.filter_w
    filter_num = design.filter_num

    # loop through the flag arrays and re-construct network, reading parameters of conv and bnorm layers
    for i in range(_num_layers):
        print('> Layer '+str(i+1))
   
        # set up conv "block" with bnorm and activation 
        net_x = set_convolutional_train(net_x, filter_h[i], filter_w[i], filter_num[i], _conv_stride[i],
                            filtergroup=_filtergroup_yn[i], batchnorm=_bnorm_yn[i], activation=_relu_yn[i], \
                            scope='conv'+str(i+1), reuse=False)
        
        # notice reuse=True for Siamese parameters sharing
        net_z = set_convolutional_train(net_z, filter_h[i], filter_w[i], filter_num[i],_conv_stride[i],
                            filtergroup=_filtergroup_yn[i], batchnorm=_bnorm_yn[i], activation=_relu_yn[i], \
                            scope='conv'+str(i+1), reuse=True)    
        
        # add max pool if required
        if _pool_stride[i]>0:
            print('\t\tMAX-POOL: size '+str(_pool_sz)+ ' and stride '+str(_pool_stride[i]))
            net_x = tf.nn.max_pool(net_x, [1,_pool_sz,_pool_sz,1], strides=[1,_pool_stride[i],_pool_stride[i],1], padding='VALID', name='pool'+str(i+1))
            net_z = tf.nn.max_pool(net_z, [1,_pool_sz,_pool_sz,1], strides=[1,_pool_stride[i],_pool_stride[i],1], padding='VALID', name='pool'+str(i+1))

    

    return net_z, net_x


def _import_from_matconvnet(net_path):
    mat = scipy.io.loadmat(net_path)
    net_dot_mat = mat.get('net')
    # organize parameters to import
    params = net_dot_mat['params']
    params = params[0][0]
    params_names = params['name'][0]
    params_names_list = [params_names[p][0] for p in range(params_names.size)]
    params_values = params['value'][0]
    params_values_list = [params_values[p] for p in range(params_values.size)]
    return params_names_list, params_values_list


# find all parameters matching the codename (there should be only one)
def _find_params(x, params):
    matching = [s for s in params if x in s]
    assert len(matching)==1, ('Ambiguous param name found')    
    return matching


def _match_templates(net_z, net_x, params_names_list, params_values_list):
    # finalize network
    # z, x are [B, H, W, C]
    net_z = tf.transpose(net_z, perm=[1,2,0,3])
    net_x = tf.transpose(net_x, perm=[1,2,0,3])
    # z, x are [H, W, B, C]
    Hz, Wz, B, C = tf.unstack(tf.shape(net_z))
    Hx, Wx, Bx, Cx = tf.unstack(tf.shape(net_x))
    # assert B==Bx, ('Z and X should have same Batch size')
    # assert C==Cx, ('Z and X should have same Channels number')
    net_z = tf.reshape(net_z, (Hz, Wz, B*C, 1))
    net_x = tf.reshape(net_x, (1, Hx, Wx, B*C))
    net_final = tf.nn.depthwise_conv2d(net_x, net_z, strides=[1,1,1,1], padding='VALID')
    #print("shape of net:", net_final.get_shape().as_list())
    # final is [1, Hf, Wf, BC]
    net_final = tf.concat(tf.split(net_final, 3, axis=3), axis=0)
    #print("shape of net_cat:", net_final.get_shape().as_list())
    # final is [B, Hf, Wf, C]
    net_final = tf.expand_dims(tf.reduce_sum(net_final, axis=3), axis=3)
    #print("shape of net_final:", net_final.get_shape().as_list())
    # final is [B, Hf, Wf, 1]
    if _bnorm_adjust:
        bn_beta = params_values_list[params_names_list.index('fin_adjust_bnb')]
        bn_gamma = params_values_list[params_names_list.index('fin_adjust_bnm')]
        bn_moments = params_values_list[params_names_list.index('fin_adjust_bnx')]
        bn_moving_mean = bn_moments[:,0]
        bn_moving_variance = bn_moments[:,1]**2
        net_final = tf.layers.batch_normalization(net_final, beta_initializer=tf.constant_initializer(bn_beta),
                                                gamma_initializer=tf.constant_initializer(bn_gamma),
                                                moving_mean_initializer=tf.constant_initializer(bn_moving_mean),
                                                moving_variance_initializer=tf.constant_initializer(bn_moving_variance),
                                                training=False, trainable=False)

    return net_final

def _match_templates_train(net_z, net_x):
    # finalize network
    # z, x are [B, H, W, C]
    print("shape_net_z:", net_z.shape)
    net_z = tf.transpose(net_z, perm=[1,2,0,3])
    net_x = tf.transpose(net_x, perm=[1,2,0,3])
    # z, x are [H, W, B, C]
    Hz, Wz, B, C = tf.unstack(tf.shape(net_z))
    Hx, Wx, Bx, Cx = tf.unstack(tf.shape(net_x))
    # assert B==Bx, ('Z and X should have same Batch size')
    # assert C==Cx, ('Z and X should have same Channels number')
    net_z = tf.reshape(net_z, (Hz, Wz, B*C, 1))
    net_x = tf.reshape(net_x, (1, Hx, Wx, B*C))
    net_final = tf.nn.depthwise_conv2d(net_x, net_z, strides=[1,1,1,1], padding='VALID')
    #print("shape of net:", net_final.get_shape().as_list())
    # final is [1, Hf, Wf, BC]
    net_final = tf.concat(tf.split(net_final, 3, axis=3), axis=0)
    #print("shape of net_cat:", net_final.get_shape().as_list())
    # final is [B, Hf, Wf, C]
    net_final = tf.expand_dims(tf.reduce_sum(net_final, axis=3), axis=3)
    
    # final is [B, Hf, Wf, 1]
    if _bnorm_adjust:
        
        net_final = tf.layers.batch_normalization(net_final)
    print("shape of net_final:", net_final.get_shape().as_list())

    return net_final