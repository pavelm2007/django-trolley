# -*- coding: utf-8 -*-

import datetime, string, random, decimal, simplejson

from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic

import settings as cart_settings
import helpers

class CartProductInterface(object):
    """Minimal CartProductInterface implementation, raising NotImplementedError
    for all required methods."""
    
    def get_thumbnail(self, options={}):
        """Returns an image instance for the product""" 
        raise NotImplementedError()

    def get_price(self, quantity, options={}):
        """Returns product price, given the quantity added and options"""
        raise NotImplementedError()


    # optional methods
    
    @staticmethod
    def complete_purchase(orderlines, order):
        """Called on purchase completion for each item in the order."""
        pass


class DefaultCartProductInterface(CartProductInterface):
    """CartProductInterface interface implementation, giving sensible defaults for
    all required methods."""
    
    def get_thumbnail(self, options={}):
        return None


class Order(models.Model):
    """Stores information that should apply to every purchase."""
    
    hash = models.CharField(max_length=16, unique=True, editable=False)
    name = models.CharField(max_length=255, default='', blank=True)
    email = models.EmailField(default='', blank=True)
    phone = models.CharField(max_length=20, default='', blank=True)
    street_address = models.CharField(max_length=255, blank=True, default='')
    suburb = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=255, blank=True, default='')
    post_code = models.CharField(max_length=20, blank=True, default='')
    country = models.CharField(max_length=255, blank=True, default='')
    
    
    status = models.CharField(max_length=20, choices=cart_settings.ORDER_STATUSES, default='pending')
    payment_successful = models.BooleanField(default=False)
    notification_sent = models.BooleanField(default=False, editable=False)
    acknowledgement_sent = models.BooleanField(default=False, editable=False)
    
    
    creation_date = models.DateTimeField(auto_now_add=True, editable=False)
    payment_date = models.DateTimeField(null=True, blank=True, editable=False)
    completion_date = models.DateTimeField(null=True, blank=True, editable=False)
    session_id = models.CharField(max_length=32, editable=False)
    
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    class Meta:
        ordering = ('-creation_date',)
    
    def __unicode__(self):
        return "Order #%s - %s, %s" % (self.pk, self.name, self.total())
    
    def save(self, *args, **kwargs):
        if (not self.completion_date) and self.status == 'shipped':
            self.completion_date = datetime.datetime.now()
        if (not self.payment_date) and self.payment_successful:
            self.payment_date = datetime.datetime.now()
            self.complete_purchase()
        
        super(Order, self).save(*args, **kwargs)

    def complete_purchase(self):
        groups = {}
        for line in self.orderline_set.all():
            ctype = ContentType.objects.get_for_model(line.product)
            if not groups.get(ctype.pk, None):
                groups[ctype.pk] = []
                
            groups[ctype.pk].append(line)
            
        for ctype_pk in groups:
            cls = ContentType.objects.get(pk=ctype_pk).model_class()
            if hasattr(cls, 'complete_purchase'):
                cls.complete_purchase(groups[ctype_pk], self)
    
    def total(self):
        total = sum(line.price for line in self.orderline_set.all()) + self.shipping_cost
        
        detail_cls = helpers.get_order_detail()
        if detail_cls:
            try:
                detail = self.get_detail()
            except detail_cls.DoesNotExist:
                pass
            else:
                total += getattr(detail, 'additional_total', lambda: 0)()
                
        return total
        
    def total_str(self, prefix='$'):
        return "%s%.2f" % (prefix, self.total())
    total_str.short_description = "Total"
    
    def total_quantity(self):
        return sum(line.quantity for line in self.orderline_set.all())
    
    @models.permalink
    def get_absolute_url(self):
        return ('cart.views.complete', (self.hash,))
    
    @models.permalink
    def get_admin_url(self):
        return ('admin:cart_order_change', (self.pk,))
    
    def get_detail(self):
        """Returns extra detail as defined by get_order_detail()"""
        if not hasattr(self, '_detail_cache'):
            model_cls = helpers.get_order_detail()
            
            if model_cls:
                self._detail_cache = model_cls._default_manager.using(self._state.db).get(order__id__exact=self.id)
                self._detail_cache.order = self
            else:
                self._detail_cache = None
        return self._detail_cache
    

class OrderLine(models.Model):
    """Stores information about a single product/options combination for an order."""
    
    order = models.ForeignKey(Order)
    
    product_content_type = models.ForeignKey(ContentType)
    product_object_id = models.PositiveIntegerField()
    product = generic.GenericForeignKey('product_content_type', 'product_object_id')    

    quantity = models.IntegerField(default=1)
    # total price for the line, not per-unit
    price = models.DecimalField(max_digits=10, decimal_places=2)
    options = models.TextField(blank=True, default='', editable=False)
    
    def options_text(self):
        if self.options:
            options = simplejson.loads(self.options)
            return ", ".join([options[key] for key in options])
        else:
            return ''
    
    class Meta:
        pass
    
    def __unicode__(self):
        return unicode(self.product) + (" (%s)" % self.options_text() if self.options else '')
        
    def latest_payment_attempt(self):
        if self.payment_attempt_set.count():
            return self.payment_attempt_set.order_by('-creation_date')[0]
        else:
            return None


class PaymentAttempt(models.Model):
    """Stores information about each attempted payment for an order."""
    
    order = models.ForeignKey(Order)
    hash = models.CharField(max_length=16, unique=True, editable=False)
    result = models.TextField(default='', blank=True)
    user_message = models.TextField(default='', blank=True)
    transaction_ref = models.CharField(max_length=32, blank=True, default='')
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    success = models.BooleanField(default=False)
    creation_date = models.DateTimeField(auto_now_add=True)
    
    def __unicode__(self):
        return "Payment attempt #%s on Order #%s" % (self.pk, self.order.pk)
    
    class Meta:
        ordering = ('-creation_date',)
        

CHARS = string.digits + string.letters

def create_hash(sender, **kwargs):
    while not kwargs['instance'].hash or PaymentAttempt.objects.filter(hash=kwargs['instance'].hash).exclude(pk=kwargs['instance'].pk):
        hash = ''.join(random.choice(CHARS) for i in xrange(8))
        kwargs['instance'].hash = hash
models.signals.pre_save.connect(create_hash, sender=PaymentAttempt)
models.signals.pre_save.connect(create_hash, sender=Order)




