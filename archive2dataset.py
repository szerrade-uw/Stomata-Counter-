#!/usr/bin/env python2
# Generate a training dataset of stoma/non-stoma images from Karl's annotated images

import os
from config import config
import csv
from collections import defaultdict
import random
import cv2
import numpy as np
import db
import shutil
from PIL import Image
import datetime
from tqdm import tqdm


def load_positions(filename):
    # Load position list formatted as rows with
    # id\tx\ty
    entries = list(csv.reader(open(filename, 'rt'), delimiter='\t'))
    positions = defaultdict(list)
    for entry in entries[1:]: # Start on second line to ignore header
        positions[entry[0]] += [[int(entry[1]), int(entry[2])]]
    return positions


def ensure_path_exists(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def get_sample_filename(pos, angle, extract_size, output_path, img_name, sample_id=None):
    # Compose filename of sample from source and extraction position and rotation
    return os.path.join(output_path, '%s_%s_%04d_%04d_%03d_%03d_%03d.jpg'
                        % (str(sample_id), img_name, pos[0], pos[1], int(angle), extract_size[0], extract_size[1]))


def extract_sample(img, pos, angle, output_path, img_name, extract_size, sample_id=None):
    filename = get_sample_filename(pos, angle, extract_size, output_path, img_name, sample_id=sample_id)
    M = cv2.getRotationMatrix2D(tuple(pos), angle, 1)
    M[0, 2] -= pos[0] - extract_size[0] / 2
    M[1, 2] -= pos[1] - extract_size[1] / 2
    sample = cv2.warpAffine(img, M, extract_size)
    cv2.imwrite(filename, sample)
    #print 'Extracted sample %s.' % filename


def extract_target_positions(img, allpos, output_path, img_name, angles, extract_size, sample_id=None):
    # Extract all given positions at all angles
    ensure_path_exists(output_path)
    n = 0
    for pos in allpos:
        for angle in angles:
            extract_sample(img, pos, angle, output_path, img_name, extract_size, sample_id=sample_id)
            n += 1
    return n


def extract_distractor_positions(img, allpos, output_path, img_name, angles, extract_size, sample_id=None):
    # Extract from positions not close to any given positions at all angles
    ensure_path_exists(output_path)
    n_distractors = len(allpos)
    n = 0
    size = img.shape
    min_margin = [c*3/2 for c in extract_size] # For now, only extract near the center because of unlabeled targets near the border
    if any([min_margin[i] >= size[1 - i] - min_margin[i] for i in (0, 1)]):
        print ('WARNING: Cannot extract from image size %s (min margin %s)' % (size, min_margin))
        return 0
    min_dist = max(extract_size)
    max_retry = 100000
    while n_distractors and max_retry:
        # Find a random position in the image (or at its border) that is not close to any targets
        pos = [np.random.random_integers(min_margin[i], size[1-i]-min_margin[i]) for i in (0, 1)]
        # Make sure it's not close to any target position
        pos_ok = True
        for tpos in allpos:
            dist = np.linalg.norm([tpos[i] - pos[i] for i in (0, 1)])
            if dist < min_dist:
                pos_ok = False
                max_retry -= 1
                break
        if not pos_ok:
            continue
        # Sample here
        for angle in angles:
            extract_sample(img, pos, angle, output_path, img_name, extract_size, sample_id=sample_id)
        n_distractors -= 1
        n += 1
    return n


def extract_positions(img_filename, allpos, output_path, img_name, angles, extract_size, sample_id=None):
    # Load image and extract both positive examples from pos as well as negative examples from elsewhere
    # Store images into output_path/target and output_path/distractor
    n = 0
    # Load image
    img = cv2.imread(img_filename)
    #for pos in allpos:
    #    cv2.circle(img, tuple(pos), 96, (255, 255, 0), thickness=1)
    #    cv2.circle(img, tuple(pos), 128, (255, 255, 0), thickness=3)
    #    cv2.circle(img, tuple(pos), 172, (127, 127, 0), thickness=3)
    # Extract positions
    try:
        n += extract_target_positions(img, allpos, os.path.join(output_path, 'target'), img_name, angles,
                                      extract_size, sample_id=sample_id)
        n += extract_distractor_positions(img, allpos, os.path.join(output_path, 'distractor'), img_name, angles,
                                          extract_size, sample_id=sample_id)
    except:
        print ('Error extracting from %s' % img_filename)
        raise
    return n


def plot_locations(img_filename, allpos, out_filename):
    # Put some red circles around the annotated locations
    # Load image
    img = cv2.imread(img_filename)
    for pos in allpos:
        cv2.circle(img, tuple(pos), 128, (255, 255, 0), thickness=3)
    cv2.imwrite(out_filename, img)
    print ('Wrote annotation to %s.' % out_filename)


def plot_all_locations(train_path, positions, output_path):
    ensure_path_exists(output_path)
    for img_name, allpos in positions.items():
        img_filename = os.path.join(train_path, img_name + '.jpg')
        plot_locations(img_filename, allpos, os.path.join(output_path, 'targets_' + img_name + '.jpg'))


# Iterate over all image files in subfolders of path and put them into the leaves dictionary by family
# Return number of leaves added
def generate_filelist_from_folder(filelists, path, img_root, category=None):
    valid_extensions = ['jpeg', 'jpg', 'png', 'tif', 'tiff']
    n = 0
    for filename in os.listdir(path):
        full_fn = os.path.join(path, filename)
        if os.path.isdir(full_fn):
            if category is None:
                n += generate_filelist_from_folder(filelists, full_fn, img_root=img_root, category=filename)
            else:
                print ('Skipping subfolder %s' % full_fn)
            continue
        if os.stat(full_fn).st_size <= 1000:
            print ('Skipping %s: Too small.' % full_fn)
            continue
        extension = full_fn.split('.')[-1].lower()
        if not extension in valid_extensions:
            print ('Filename %s has unknown extension.' % full_fn)
            continue
        if category is None:
            print ('Filename %s has no category.' % full_fn)
            continue
        filelists[category] += [os.path.relpath(full_fn, img_root)]
        n += 1
    return n


def generate_filelist(path, img_root):
    filelists = defaultdict(list)
    n = generate_filelist_from_folder(filelists, path, img_root)
    return filelists


def generate_image_patches(train_path, positions, output_path, angles, extract_size):
    n_total = 0
    for img_name, allpos in positions.iteritems():
        img_filename = os.path.join(train_path, img_name + '.jpg')
        if not os.path.isfile(img_filename):
            print ('Skipping missing file %s.' % img_filename)
            continue
        n = len(allpos)
        n_total += n
        print ('%02d samples in file %s.' % (n, img_filename))
        extract_positions(img_filename, allpos, output_path, img_name, angles, extract_size)
    print ('%d samples total.' % n_total)


def archive2dataset():
    extract_size = (256, 256) # wdt, hgt
    n_angles = 8
    angles = np.linspace(0, 360, num=n_angles, endpoint=False)
    train_path = os.path.join(config.get_data_path(), 'Pb_stomata_09_03_16_Archive')
    positions = load_positions(os.path.join(train_path, 'VT_stomata_xy_trial_10_15_16.txt'))
    #plot_all_locations(train_path, positions, os.path.join(data_path, 'epi_targets'))
    output_path = os.path.join(config.get_data_path(), 'epi1')
    #generate_image_patches(train_path, positions, output_path, angles, extract_size)
    # Generate dataset text files
    filelist = generate_filelist(output_path, config.get_data_path())
    class_indices = {'distractor': 0, 'target': 1}
    train, val, test = split_train_test(filelist, n_test=100, n_val=100)
    save_filelist_shuffled(train, class_indices, os.path.join(config.get_data_path(), 'epi1_train.txt'))
    save_filelist_shuffled(val, class_indices, os.path.join(config.get_data_path(), 'epi1_val.txt'))
    save_filelist_shuffled(test, class_indices, os.path.join(config.get_data_path(), 'epi1_test.txt'))


def dbpos2extpos(pos):
    return (pos['x'], pos['y'])


def db2patches(output_path, train_label=None, sample_limit=None):
    # All human annotations in DB converted to a training set
    n_angles = 8
    angles = np.linspace(0, 360, num=n_angles, endpoint=False)
    extract_size = (256, 256)  # wdt, hgt
    annotated_samples = db.get_human_annotated_samples(train_label=train_label)
    n_annotated_samples = len(annotated_samples)
    if sample_limit is not None and n_annotated_samples > sample_limit:
        print ('Reducing from %d to sample limit %d...' % (n_annotated_samples, sample_limit))
        annotated_samples = np.random.choice(annotated_samples, sample_limit, replace=False)
    print ('Extracting patches from %d images to %s...' % (len(annotated_samples), output_path))
    n = 0
    for s in tqdm(annotated_samples):
        img_filename = os.path.join(config.get_server_image_path(), s['filename'])
        img_name = s['filename'].replace('.', '_')
        for annotation in db.get_human_annotations(s['_id']):
            allpos = [dbpos2extpos(p) for p in annotation['positions']]
            n += extract_positions(img_filename, allpos, output_path, img_name, angles, extract_size, s['_id'])
    print ('%d patches extracted.' % n)


def patches2filelist(output_path):
    filelist = generate_filelist(output_path, output_path)
    class_indices = {'distractor': 0, 'target': 1}
    train, val, test = split_train_test(filelist, n_test=100, n_val=100)
    save_filelist_shuffled(train, class_indices, os.path.join(output_path, 'train.txt'))
    save_filelist_shuffled(val, class_indices, os.path.join(output_path, 'val.txt'))
    save_filelist_shuffled(test, class_indices, os.path.join(output_path, 'test.txt'))


def gen_new_output_path():
    idtf = datetime.datetime.now().strftime('%Y_%m_%d_%H_%M')
    output_path = os.path.join(config.get_train_data_path(), 'DS_' + idtf)
    while os.path.isdir(output_path): output_path += '_'
    os.makedirs(output_path)
    return output_path


def get_karl_dataset_id():
    name = 'Pb_stomata_09_03_16_Archive'
    ds = db.get_dataset_by_name(name)
    if ds is None:
        ds = db.add_dataset(name)
    return ds['_id']


def pos2db(p):
    return { 'x': p[0], 'y': p[1] }


def import_karl_labels():
    dataset_id = get_karl_dataset_id()
    train_path = os.path.join(config.get_data_path(), 'Pb_stomata_09_03_16_Archive')
    positions = load_positions(os.path.join(train_path, 'VT_stomata_xy_trial_10_15_16.txt'))
    for fn, pos in positions.iteritems():
        pos_db = [pos2db(p) for p in pos]
        fnj = fn + '.jpg'
        fn_full = os.path.join(train_path, fnj)
        im = Image.open(fn_full)
        filename = os.path.basename(fnj)
        fn_target = os.path.join(config.get_server_image_path(), filename)
        shutil.copyfile(fn_full, fn_target)
        sample = db.add_sample(os.path.basename(fn_target), size=im.size, dataset_id=dataset_id)
        sample_id = sample['_id']
        db.set_human_annotation(sample_id, None, pos_db, margin=32)
        print ('http://0.0.0.0:9000/info/%s' % str(sample_id))

    print (train_path)
    print (positions)


# Save filenames and class indices into text file
def save_filelist_shuffled(leaves, family_indices, save_file, force_overwrite=False):
    if (not force_overwrite) and os.path.isfile(save_file):
        print ('Skip existing file %s.' % save_file)
        return
    leaves_list = leaves_to_list(leaves, family_indices)
    random.shuffle(leaves_list)
    with open(save_file, 'wt') as fid:
        for entry in leaves_list:
            fid.write('%s %d\n' % (entry[0], int(entry[1])))
    print ('%d entries written to %s.' % (len(leaves_list), save_file))



# Convert family-indexed dictionary to list of (filename, class_index) pairs
def leaves_to_list(leaves, family_indices):
    # Collect list of (name, class_index) pairs
    # Pre-allocate list
    n = 0
    for leaves_f in leaves.values():
        n += len(leaves_f)
    leaves_list = [None] * n
    i = 0
    # Fill it
    for family, leaves_f in leaves.items():
        family_index = family_indices[family]
        for entry in leaves_f:
            if ' ' in entry or '?' in entry:
                print ('WARNING: %s has a space or question mark.' % entry)
            leaves_list[i] = (entry, family_index)
            i += 1
    return leaves_list


# Split a train and test set
def split_train_test(leaves, n_test, n_val=0, test_ratio=None, val_ratio=None, test_threshold=None):
    leaves_train = dict()
    leaves_val = dict()
    leaves_test = dict()
    for family, leaves_f in leaves.items():
        n = len(leaves_f)
        if test_ratio is not None:
            n_test = 0
            if family != 'unknown':
                if (test_threshold is None) or (n >= test_threshold):
                    n_test = int(round(test_ratio*n))
        if val_ratio is not None:
            n_val = int(round(val_ratio*n))
        if n_test + n_val > n:
            raise RuntimeError('ERROR: Requesting %d+%d test samples from family %s of size %d.' % (n_test, n_val, family, n))
        test_sample = set(random.sample(range(n), n_test))
        nontest_sample = [i for i in range(n) if i not in test_sample]
        val_sample = set(random.sample(nontest_sample, n_val))
        train_sample = [i for i in nontest_sample if i not in val_sample]
        leaves_test[family] = [fn for i,fn in enumerate(leaves_f) if i in test_sample]
        leaves_val[family] = [fn for i, fn in enumerate(leaves_f) if i in val_sample]
        leaves_train[family] = [fn for i, fn in enumerate(leaves_f) if i in train_sample]
    return leaves_train, leaves_val, leaves_test
