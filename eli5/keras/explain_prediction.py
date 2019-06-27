# -*- coding: utf-8 -*-
from __future__ import absolute_import
from typing import Union, Optional, Callable, Tuple, List

import numpy as np # type: ignore
import keras # type: ignore
import keras.backend as K # type: ignore
from keras.models import Model # type: ignore
from keras.layers import Layer # type: ignore

from eli5.base import Explanation
from eli5.explain import explain_prediction
from .gradcam import gradcam, gradcam_backend


DESCRIPTION_KERAS = """Grad-CAM visualization for image classification; output is explanation
object that contains input image and heatmap image."""

# note that keras.models.Sequential subclasses keras.models.Model
@explain_prediction.register(Model)
def explain_prediction_keras(estimator, # type: Model
                             doc, # type: np.ndarray
                             target_names=None,
                             targets=None, # type: Optional[list]
                             layer=None, # type: Optional[Union[int, str, Layer]]
                            ):
    # type: (...) -> Explanation
    # FIXME: in docs rendered param order is "type, required (paramname)", should be other way around
    """
    Explain the prediction of a Keras image classifier.

    We make two explicit assumptions
        * The input is images.
        * The model's task is classification, i.e. final output is class scores.

    See :func:`eli5.explain_prediction` for more information about the ``estimator``,
    ``doc``, ``target_names``, and ``targets`` parameters.

    
    :param estimator `keras.models.Model`:
        Instance of a Keras neural network model, 
        whose predictions are to be explained.


    :param doc `numpy.ndarray`:
        An input image as a tensor to ``estimator``, 
        from which prediction will be done and explained.

        For example a ``numpy.ndarray``.

        The tensor must be of suitable shape for the ``estimator``. 

        For example, some models require input images to be 
        rank 4 in format `(batch_size, dims, ..., channels)` (channels last)
        or `(batch_size, channels, dims, ...)` (channels first), 
        where `dims` is usually in order `height, width`
        and `batch_size` is 1 for a single image.

        Check ``estimator.input_shape`` to confirm the required dimensions of the input tensor.


        :raises TypeError: if ``doc`` is not a numpy array.
        :raises ValueError: if ``doc`` shape does not match.

    :param target_names `list, optional`:
        *Not Implemented*. 

        Names for classes in the final output layer.

    :param targets `list[int], optional`:
        Prediction ID's to focus on.

        *Currently only the first prediction from the list is explained*. 
        The list must be length one.

        If None, the model is fed the input image and its top prediction 
        is taken as the target automatically.


        :raises ValueError: if targets is a list with more than one item.
        :raises TypeError: if targets is not list or None.

    :param layer `int or str or keras.layers.Layer, optional`:
        The activation layer in the model to perform Grad-CAM on,
        a valid keras layer name, layer index, or an instance of a Keras layer.
        
        If None, a suitable layer is attempted to be retrieved. 
        See :func:`eli5.keras._search_layer_backwards` for details.


        :raises TypeError: if ``layer`` is not None, str, int, or keras.layers.Layer instance.
        :raises ValueError: if suitable layer can not be found.


    Returns
    -------
    expl : Explanation
        A :class:`eli5.base.Explanation` object 
        with the ``image`` and ``heatmap`` attributes set.

        ``image`` is a Pillow image with mode RGBA.

        ``heatmap`` is a rank 2 numpy array with the localization map values.
    """
    _validate_doc(estimator, doc)
    activation_layer = _get_activation_layer(estimator, layer)
    
    weights, activations, grads, predicted_idx, score = gradcam_backend(estimator, doc, targets, activation_layer)
    heatmap = gradcam(weights, activations)

    print('Predicted class: %d' % predicted_idx)
    print('With probability: %f' % score)

    doc = doc[0] # rank 4 batch -> rank 3 single image
    image = keras.preprocessing.image.array_to_img(doc) # -> RGB Pillow image
    image = image.convert(mode='RGBA')

    return Explanation(
        estimator.name,
        description=DESCRIPTION_KERAS,
        error='',
        method='Grad-CAM',
        is_regression=False, # might be relevant later when explaining for regression tasks
        highlight_spaces=None, # might be relevant later when explaining text models
        image=image, # RGBA Pillow image
        heatmap=heatmap, # 2D [0, 1] numpy array
    )


def _validate_doc(estimator, doc):
    # type: (Model, np.ndarray) -> None
    """
    Check that the input ``doc`` is suitable for ``estimator``.
    """
    if not isinstance(doc, np.ndarray):
        raise TypeError('doc must be a numpy.ndarray, got: {}'.format(doc))
    input_sh = estimator.input_shape
    doc_sh = doc.shape
    if len(input_sh) == 4:
        # rank 4 with (batch, ...) shape
        # check that we have only one image (batch size 1)
        single_batch = (1,) + input_sh[1:]
        if doc_sh != single_batch:
            raise ValueError('Batch size does not match (must be 1). ' 
                             'doc must be of shape: {}, '
                             'got: {}'.format(single_batch, doc_sh))
    else:
        # other shapes
        if doc_sh != input_sh:
            raise ValueError('Input and doc shapes do not match.'
                             'input: {}, doc: {}'.format(input_sh, doc_sh))


def _get_activation_layer(estimator, layer):
    # type: (Model, Union[None, int, str, Layer]) -> Layer
    """
    Get an instance of the desired activation layer in ``estimator``,
    as specified by ``layer``.
    """        
    if layer is None:
        # Automatically get the layer if not provided
        activation_layer = _search_layer_backwards(estimator, _is_suitable_activation_layer)
        return activation_layer

    if isinstance(layer, Layer):
        activation_layer = layer
    # get_layer() performs a bottom-up horizontal graph traversal
    # it can raise ValueError if the layer index / name specified is not found
    elif isinstance(layer, int):
        activation_layer = estimator.get_layer(index=layer)
    elif isinstance(layer, str):
        activation_layer = estimator.get_layer(name=layer)
    else:
        raise TypeError('Invalid layer (must be str, int, keras.layers.Layer, or None): %s' % layer)

    if _is_suitable_activation_layer(estimator, activation_layer):
        # final validation step
        return activation_layer
    else:
        raise ValueError('Can not perform Grad-CAM on the retrieved activation layer')


def _search_layer_backwards(estimator, condition):
    # type: (Model, Callable[[Model, int], bool]) -> Layer
    """
    Search for a layer in ``estimator``, backwards (starting from the output layer),
    checking if the layer is suitable with the callable ``condition``,
    """
    # linear search in reverse through the flattened layers
    for layer in estimator.layers[::-1]:
        if condition(estimator, layer):
            # linear search succeeded
            return layer
    # linear search ended with no results
    raise ValueError('Could not find a suitable target layer automatically.')        


def _is_suitable_activation_layer(estimator, layer):
    # type: (Model, Layer) -> bool
    """
    Check whether the layer ``layer`` matches what is required 
    by ``estimator`` to do Grad-CAM on ``layer``.
    Returns a boolean.
    
    Matching Criteria:
        * Rank of the layer's output tensor.
    """
    # TODO: experiment with this, using many models and images, to find what works best
    # Some ideas: 
    # check layer type, i.e.: isinstance(l, keras.layers.Conv2D)
    # check layer name

    # a check that asks "can we resize this activation layer over the image?"
    rank = len(layer.output_shape)
    required_rank = len(estimator.input_shape)
    return rank == required_rank