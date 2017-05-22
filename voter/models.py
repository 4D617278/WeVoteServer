# voter/models.py
# Brought to you by We Vote. Be good.
# -*- coding: UTF-8 -*-

from django.db import models
from django.db.models import Q
from django.contrib.auth.models import (BaseUserManager, AbstractBaseUser)  # PermissionsMixin
from django.core.validators import RegexValidator
from exception.models import handle_exception, handle_record_found_more_than_one_exception,\
    handle_record_not_saved_exception
from import_export_facebook.models import FacebookManager
from twitter.models import TwitterUserManager
from validate_email import validate_email
import wevote_functions.admin
from wevote_functions.functions import convert_to_int, generate_voter_device_id, get_voter_device_id, \
    get_voter_api_device_id, positive_value_exists
from wevote_settings.models import fetch_next_we_vote_id_last_voter_integer, fetch_site_unique_id_prefix


logger = wevote_functions.admin.get_logger(__name__)
SUPPORT_OPPOSE_MODAL_SHOWN = 1


# This way of extending the base user described here:
# https://docs.djangoproject.com/en/1.8/topics/auth/customizing/#a-full-example
# I then altered with this: http://buildthis.com/customizing-djangos-default-user-model/


# class VoterTwitterLink(models.Model):
#     voter_id
#     twitter_handle
#     confirmed_signin_date


# See AUTH_USER_MODEL in config/base.py
class VoterManager(BaseUserManager):
    def clear_out_collisions_for_linked_organization_we_vote_id(self, current_voter_we_vote_id,
                                                                organization_we_vote_id):
        status = ""
        success = True
        collision_results = self.retrieve_voter_by_organization_we_vote_id(
            organization_we_vote_id)
        if collision_results['voter_found']:
            collision_voter = collision_results['voter']
            if collision_voter.we_vote_id != current_voter_we_vote_id:
                # Release the linked_organization_we_vote_id from collision_voter so it can be used on voter
                try:
                    collision_voter.linked_organization_we_vote_id = None
                    collision_voter.save()

                    # TODO DALE UPDATE positions to remove voter_we_vote_id
                    # Since we are disconnecting the organization from the voter, do we want to go through
                    # positions and split them apart?
                except Exception as e:
                    success = False
                    status += " UNABLE_TO_UPDATE_COLLISION_VOTER_WITH_EMPTY_ORGANIZATION_WE_VOTE_ID"
        results = {
            'success':  success,
            'status':   status
        }
        return results

    def create_user(self, email=None, username=None, password=None):
        """
        Creates and saves a User with the given email and password.
        """
        email = self.normalize_email(email)
        user = self.model(email=self.normalize_email(email))

        # python-social-auth will pass the username and email
        if username:
            user.fb_username = username

        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password):
        """
        Creates and saves a superuser with the given email and password.
        """
        user = self.create_user(email, password=password)
        user.is_admin = True
        user.save(using=self._db)
        return user

    def create_voter(self, email=None, password=None):
        email = self.normalize_email(email)
        email_not_valid = False
        password_not_valid = False

        voter = Voter()
        voter_id = 0
        try:
            if validate_email(email):
                voter.email = email
            else:
                email_not_valid = True

            if password:
                voter.set_password(password)
            else:
                password_not_valid = True
            voter.save()
            voter_id = voter.id
        except voter.IntegrityError as e:
            handle_record_not_saved_exception(e, logger=logger)
            try:
                # Trying to save again will increment the 'we_vote_id_last_voter_integer'
                # by calling 'fetch_next_we_vote_id_last_voter_integer'
                # TODO We could get into a race condition where multiple creates could be failing at once, so we
                #  should look more closely at this
                voter.save()
                voter_id = voter.id
            except voter.IntegrityError as e:
                handle_record_not_saved_exception(e, logger=logger)
            except Exception as e:
                handle_record_not_saved_exception(e, logger=logger)

        except Exception as e:
            handle_record_not_saved_exception(e, logger=logger)

        results = {
            'email_not_valid':      email_not_valid,
            'password_not_valid':   password_not_valid,
            'voter_created':        True if voter_id > 0 else False,
            'voter':                voter,
        }
        return results

    def delete_voter(self, email):
        email = self.normalize_email(email)
        voter_id = 0
        voter_we_vote_id = ''
        voter_deleted = False

        if positive_value_exists(email) and validate_email(email):
            email_valid = True
        else:
            email_valid = False

        try:
            if email_valid:
                results = self.retrieve_voter(voter_id, email, voter_we_vote_id)
                if results['voter_found']:
                    voter = results['voter']
                    voter_id = voter.id
                    voter.delete()
                    voter_deleted = True
        except Exception as e:
            handle_exception(e, logger=logger)

        results = {
            'email_not_valid':      True if not email_valid else False,
            'voter_deleted':        voter_deleted,
            'voter_id':             voter_id,
        }
        return results

    def retrieve_voter_from_voter_device_id(self, voter_device_id):
        voter_id = fetch_voter_id_from_voter_device_link(voter_device_id)

        if not voter_id:
            results = {
                'voter_found':  False,
                'voter_id':     0,
                'voter':        Voter(),
            }
            return results

        voter_manager = VoterManager()
        results = voter_manager.retrieve_voter_by_id(voter_id)
        if results['voter_found']:
            voter_on_stage = results['voter']
            voter_on_stage_found = True
            voter_id = results['voter_id']
        else:
            voter_on_stage = Voter()
            voter_on_stage_found = False
            voter_id = 0

        results = {
            'voter_found':  voter_on_stage_found,
            'voter_id':     voter_id,
            'voter':        voter_on_stage,
        }
        return results

    def fetch_we_vote_id_from_local_id(self, voter_id):
        results = self.retrieve_voter_by_id(voter_id)
        if results['voter_found']:
            voter = results['voter']
            return voter.we_vote_id
        else:
            return None

    def fetch_local_id_from_we_vote_id(self, voter_we_vote_id):
        results = self.retrieve_voter_by_we_vote_id(voter_we_vote_id)
        if results['voter_found']:
            voter = results['voter']
            return voter.id
        else:
            return 0

    def fetch_facebook_id_from_voter_we_vote_id(self, voter_we_vote_id):
        if positive_value_exists(voter_we_vote_id):
            facebook_manager = FacebookManager()
            facebook_id = facebook_manager.fetch_facebook_id_from_voter_we_vote_id(voter_we_vote_id)
        else:
            facebook_id = 0

        return facebook_id

    def fetch_twitter_id_from_voter_we_vote_id(self, voter_we_vote_id):
        if positive_value_exists(voter_we_vote_id):
            twitter_user_manager = TwitterUserManager()
            voter_twitter_id = twitter_user_manager.fetch_twitter_id_from_voter_we_vote_id(voter_we_vote_id)
        else:
            voter_twitter_id = ''

        return voter_twitter_id

    def fetch_twitter_handle_from_voter_we_vote_id(self, voter_we_vote_id):
        if positive_value_exists(voter_we_vote_id):
            twitter_user_manager = TwitterUserManager()
            voter_twitter_handle = twitter_user_manager.fetch_twitter_handle_from_voter_we_vote_id(voter_we_vote_id)
        else:
            voter_twitter_handle = ''

        return voter_twitter_handle

    def retrieve_voter_by_id(self, voter_id):
        email = ''
        voter_we_vote_id = ''
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id)

    def retrieve_voter_by_email(self, email):
        voter_id = ''
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email)

    def retrieve_voter_by_we_vote_id(self, voter_we_vote_id):
        voter_id = ''
        email = ''
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id)

    def retrieve_voter_by_twitter_request_token(self, twitter_request_token):
        voter_id = ''
        email = ''
        voter_we_vote_id = ''
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id, twitter_request_token)

    def retrieve_voter_by_facebook_id(self, facebook_id):
        voter_id = ''
        email = ''
        voter_we_vote_id = ''

        facebook_manager = FacebookManager()
        facebook_retrieve_results = facebook_manager.retrieve_facebook_link_to_voter(facebook_id)
        if facebook_retrieve_results['facebook_link_to_voter_found']:
            facebook_link_to_voter = facebook_retrieve_results['facebook_link_to_voter']
            voter_we_vote_id = facebook_link_to_voter.voter_we_vote_id

        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id)

    def retrieve_voter_by_facebook_id_old(self, facebook_id):
        """
        This method should only be used to heal old data.
        :param facebook_id:
        :return:
        """
        voter_id = ''
        email = ''
        voter_we_vote_id = ''
        twitter_request_token = ''
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id, twitter_request_token, facebook_id)

    def retrieve_voter_by_twitter_id(self, twitter_id):
        voter_id = ''
        email = ''
        voter_we_vote_id = ''

        twitter_user_manager = TwitterUserManager()
        twitter_retrieve_results = twitter_user_manager.retrieve_twitter_link_to_voter_from_twitter_user_id(twitter_id)
        if twitter_retrieve_results['twitter_link_to_voter_found']:
            twitter_link_to_voter = twitter_retrieve_results['twitter_link_to_voter']
            voter_we_vote_id = twitter_link_to_voter.voter_we_vote_id

        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id)

    def retrieve_voter_by_twitter_id_old(self, twitter_id):
        """
        This is a function we want to eventually deprecate as we move away from storing the twitter_id
        in the voter table
        :param twitter_id:
        :return:
        """
        voter_id = ''
        email = ''
        voter_we_vote_id = ''
        twitter_request_token = ''
        facebook_id = 0
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id, twitter_request_token, facebook_id,
                                            twitter_id)

    def retrieve_voter_by_organization_we_vote_id(self, organization_we_vote_id):
        voter_id = ''
        email = ''
        voter_we_vote_id = ''
        twitter_request_token = ''
        facebook_id = 0
        twitter_id = 0
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id, twitter_request_token, facebook_id,
                                            twitter_id, organization_we_vote_id)

    def retrieve_voter_by_primary_email_we_vote_id(self, primary_email_we_vote_id):
        voter_id = ''
        email = ''
        voter_we_vote_id = ''
        twitter_request_token = ''
        facebook_id = 0
        twitter_id = 0
        organization_we_vote_id = ''
        voter_manager = VoterManager()
        return voter_manager.retrieve_voter(voter_id, email, voter_we_vote_id, twitter_request_token, facebook_id,
                                            twitter_id, organization_we_vote_id, primary_email_we_vote_id)

    def retrieve_voter(self, voter_id, email='', voter_we_vote_id='', twitter_request_token='', facebook_id=0,
                       twitter_id=0, organization_we_vote_id='', primary_email_we_vote_id=''):
        voter_id = convert_to_int(voter_id)
        if not validate_email(email):
            # We do not want to search for an invalid email
            email = None
        if positive_value_exists(voter_we_vote_id):
            voter_we_vote_id = voter_we_vote_id.strip().lower()
        if positive_value_exists(organization_we_vote_id):
            organization_we_vote_id = organization_we_vote_id.strip().lower()
        error_result = False
        exception_does_not_exist = False
        exception_multiple_object_returned = False
        voter_on_stage = Voter()

        try:
            if positive_value_exists(voter_id):
                voter_on_stage = Voter.objects.get(id=voter_id)
                # If still here, we found an existing voter
                voter_id = voter_on_stage.id
                success = True
            elif email is not '' and email is not None:
                voter_queryset = Voter.objects.all()
                voter_queryset = voter_queryset.filter(Q(email__iexact=email))
                voter_list = list(voter_queryset[:1])
                if len(voter_list):
                    voter_on_stage = voter_list[0]
                    voter_id = voter_on_stage.id
                    success = True
                else:
                    voter_on_stage = Voter()
                    voter_id = 0
                    success = True
            elif positive_value_exists(voter_we_vote_id):
                voter_on_stage = Voter.objects.get(
                    we_vote_id__iexact=voter_we_vote_id)
                # If still here, we found an existing voter
                voter_id = voter_on_stage.id
                success = True
            elif positive_value_exists(twitter_request_token):
                voter_on_stage = Voter.objects.get(
                    twitter_request_token=twitter_request_token)
                # If still here, we found an existing voter
                voter_id = voter_on_stage.id
                success = True
            elif positive_value_exists(facebook_id):
                # 2016-11-22 This is only used to heal data. When retrieving by facebook_id,
                # we use the FacebookLinkToVoter table
                # TODO DALE Remove voter.facebook_id value - We are removing direct retrieve based on this field
                voter_on_stage = Voter.objects.get(
                    facebook_id=facebook_id)
                # If still here, we found an existing voter
                voter_id = voter_on_stage.id
                success = True
            elif positive_value_exists(twitter_id):
                # 2016-11-22 This is only used to heal data. When retrieving by twitter_id,
                # we use the TwitterLinkToVoter table
                # TODO DALE Remove voter.twitter_id value - We are removing direct retrieve based on this field
                # We put this in an extra try block because there might be multiple voters with twitter_id
                try:
                    voter_on_stage = Voter.objects.get(
                        twitter_id=twitter_id)
                    # If still here, we found a single existing voter
                    voter_id = voter_on_stage.id
                    success = True
                except Voter.MultipleObjectsReturned as e:
                    # If there are multiple entries, we do not want to guess which one to use here
                    voter_on_stage = Voter()
                    voter_id = 0
                    success = False
                except Voter.DoesNotExist as e:
                    error_result = True
                    exception_does_not_exist = True
                    success = True
            elif positive_value_exists(organization_we_vote_id):
                voter_on_stage = Voter.objects.get(
                    linked_organization_we_vote_id__iexact=organization_we_vote_id)
                # If still here, we found an existing voter
                voter_id = voter_on_stage.id
                success = True
            elif positive_value_exists(primary_email_we_vote_id):
                voter_on_stage = Voter.objects.get(
                    primary_email_we_vote_id__iexact=primary_email_we_vote_id)
                # If still here, we found an existing voter
                voter_id = voter_on_stage.id
                success = True
            else:
                voter_id = 0
                error_result = True
                success = False
        except Voter.MultipleObjectsReturned as e:
            handle_record_found_more_than_one_exception(e, logger=logger)
            error_result = True
            exception_multiple_object_returned = True
            success = False
        except Voter.DoesNotExist as e:
            error_result = True
            exception_does_not_exist = True
            success = True

        results = {
            'success':                  success,
            'error_result':             error_result,
            'DoesNotExist':             exception_does_not_exist,
            'MultipleObjectsReturned':  exception_multiple_object_returned,
            'voter_found':              True if voter_id > 0 else False,
            'voter_id':                 voter_id,
            'voter':                    voter_on_stage,
        }
        return results

    def create_voter_with_voter_device_id(self, voter_device_id):
        logger.info("create_voter_with_voter_device_id(voter_device_id)")

    def clear_out_abandoned_voter_records(self):
        # We will need a method that identifies and deletes abandoned voter records that don't have enough information
        #  to ever be used
        logger.info("clear_out_abandoned_voter_records")

    def remove_voter_cached_email_entries_from_email_address_object(self, email_address_object):
        status = ""
        success = False

        voter_manager = VoterManager()
        if positive_value_exists(email_address_object.normalized_email_address):
            voter_found_by_email_results = voter_manager.retrieve_voter_by_email(
                email_address_object.normalized_email_address)
            if voter_found_by_email_results['voter_found']:
                voter_found_by_email = voter_found_by_email_results['voter']

                # Wipe this voter's email values...
                try:
                    voter_found_by_email.email = None
                    voter_found_by_email.primary_email_we_vote_id = None
                    voter_found_by_email.email_ownership_is_verified = False
                    voter_found_by_email.save()
                    status += "ABLE_TO_CLEAN_OUT_VOTER_FOUND_BY_EMAIL "
                    success = True
                except Exception as e:
                    status += "UNABLE_TO_CLEAN_OUT_VOTER_FOUND_BY_EMAIL "

        if positive_value_exists(email_address_object.we_vote_id):
            voter_by_primary_email_results = voter_manager.retrieve_voter_by_primary_email_we_vote_id(
                email_address_object.we_vote_id)
            if voter_by_primary_email_results['voter_found']:
                voter_found_by_primary_email_we_vote_id = voter_by_primary_email_results['voter']

                # Wipe this voter's email values...
                try:
                    voter_found_by_primary_email_we_vote_id.email = None
                    voter_found_by_primary_email_we_vote_id.primary_email_we_vote_id = None
                    voter_found_by_primary_email_we_vote_id.email_ownership_is_verified = False
                    voter_found_by_primary_email_we_vote_id.save()
                    status += "ABLE_TO_CLEAN_OUT_VOTER_FOUND_BY_PRIMARY_EMAIL_WE_VOTE_ID "
                    success = True
                except Exception as e:
                    status += "UNABLE_TO_CLEAN_OUT_VOTER_FOUND_BY_PRIMARY_EMAIL_WE_VOTE_ID "

        results = {
            'success': success,
            'status': status,
        }
        return results

    def save_facebook_user_values(self, voter, facebook_auth_response,
                                  cached_facebook_profile_image_url_https=None,
                                  we_vote_hosted_profile_image_url_large=None,
                                  we_vote_hosted_profile_image_url_medium=None,
                                  we_vote_hosted_profile_image_url_tiny=None):
        try:
            voter.facebook_id = facebook_auth_response.facebook_user_id
            voter.first_name = facebook_auth_response.facebook_first_name
            voter.middle_name = facebook_auth_response.facebook_middle_name
            voter.last_name = facebook_auth_response.facebook_last_name
            if positive_value_exists(cached_facebook_profile_image_url_https):
                voter.facebook_profile_image_url_https = cached_facebook_profile_image_url_https
            else:
                voter.facebook_profile_image_url_https = facebook_auth_response.facebook_profile_image_url_https
            if positive_value_exists(we_vote_hosted_profile_image_url_large):
                voter.we_vote_hosted_profile_image_url_large = we_vote_hosted_profile_image_url_large
            if positive_value_exists(we_vote_hosted_profile_image_url_medium):
                voter.we_vote_hosted_profile_image_url_medium = we_vote_hosted_profile_image_url_medium
            if positive_value_exists(we_vote_hosted_profile_image_url_tiny):
                voter.we_vote_hosted_profile_image_url_tiny = we_vote_hosted_profile_image_url_tiny

            voter.save()
            success = True
            status = "SAVED_VOTER_FACEBOOK_VALUES"
        except Exception as e:
            status = "UNABLE_TO_SAVE_VOTER_FACEBOOK_VALUES"
            success = False

        results = {
            'status':   status,
            'success':  success,
            'voter':    voter,
        }
        return results

    def save_twitter_user_values(self, voter, twitter_user_object,
                                 cached_twitter_profile_image_url_https=None,
                                 we_vote_hosted_profile_image_url_large=None,
                                 we_vote_hosted_profile_image_url_medium=None,
                                 we_vote_hosted_profile_image_url_tiny=None):
        """
        This is used to store the cached values in the voter record after authentication.
        Please also see import_export_twitter/models.py TwitterAuthResponse->save_twitter_auth_values
        :param voter:
        :param twitter_user_object:
        :param cached_twitter_profile_image_url_https:
        :param we_vote_hosted_profile_image_url_large:
        :param we_vote_hosted_profile_image_url_medium:
        :param we_vote_hosted_profile_image_url_tiny:
        :return:
        """
        try:
            voter_to_save = False
            # TODO DALE Remove voter.twitter_id value
            if hasattr(twitter_user_object, "id") and positive_value_exists(twitter_user_object.id):
                voter.twitter_id = twitter_user_object.id
                voter_to_save = True
            # 'id_str': '132728535',
            # 'utc_offset': 32400,
            # 'description': "Cars, Musics, Games, Electronics, toys, food, etc... I'm just a typical boy!",
            # 'profile_image_url': 'http://a1.twimg.com/profile_images/1213351752/_2_2__normal.jpg',
            if positive_value_exists(cached_twitter_profile_image_url_https):
                voter.twitter_profile_image_url_https = cached_twitter_profile_image_url_https
                voter_to_save = True
            elif hasattr(twitter_user_object, "profile_image_url_https") and \
                    positive_value_exists(twitter_user_object.profile_image_url_https):
                voter.twitter_profile_image_url_https = twitter_user_object.profile_image_url_https
                voter_to_save = True
            if positive_value_exists(we_vote_hosted_profile_image_url_large):
                voter.we_vote_hosted_profile_image_url_large = we_vote_hosted_profile_image_url_large
                voter_to_save = True
            if positive_value_exists(we_vote_hosted_profile_image_url_medium):
                voter.we_vote_hosted_profile_image_url_medium = we_vote_hosted_profile_image_url_medium
                voter_to_save = True
            if positive_value_exists(we_vote_hosted_profile_image_url_tiny):
                voter.we_vote_hosted_profile_image_url_tiny = we_vote_hosted_profile_image_url_tiny
                voter_to_save = True
            # 'profile_background_image_url': 'http://a2.twimg.com/a/1294785484/images/themes/theme15/bg.png',
            # 'screen_name': 'jaeeeee',
            if hasattr(twitter_user_object, "screen_name") and positive_value_exists(twitter_user_object.screen_name):
                voter.twitter_screen_name = twitter_user_object.screen_name
                voter_to_save = True
            # 'lang': 'en',
            if hasattr(twitter_user_object, "name") and positive_value_exists(twitter_user_object.name):
                voter.twitter_name = twitter_user_object.name
                voter_to_save = True
            # 'url': 'http://www.carbonize.co.kr',
            # 'time_zone': 'Seoul',
            if voter_to_save:
                voter.save()
            success = True
            status = "SAVED_VOTER_TWITTER_VALUES"
        except Exception as e:
            status = "UNABLE_TO_SAVE_VOTER_TWITTER_VALUES"
            success = False

        results = {
            'status':   status,
            'success':  success,
            'voter':    voter,
        }
        return results

    def save_twitter_user_values_from_twitter_auth_response(self, voter, twitter_auth_response,
                                                            cached_twitter_profile_image_url_https = None,
                                                            we_vote_hosted_profile_image_url_large = None,
                                                            we_vote_hosted_profile_image_url_medium = None,
                                                            we_vote_hosted_profile_image_url_tiny = None):
        """
        This is used to store the cached values in the voter record from the twitter_auth_response object once
        voter agrees to a merge.
        NOTE 2016-10-21 Do NOT save TwitterAuthResponse values -- only photo and "soft" data
        :param voter:
        :param twitter_auth_response:
        :param cached_twitter_profile_image_url_https:
        :param we_vote_hosted_profile_image_url_large:
        :param we_vote_hosted_profile_image_url_medium:
        :param we_vote_hosted_profile_image_url_tiny:
        :return:
        """
        try:
            voter_to_save = False
            # TODO DALE Remove voter.twitter_id value
            if hasattr(twitter_auth_response, "twitter_id") and positive_value_exists(twitter_auth_response.twitter_id):
                voter.twitter_id = twitter_auth_response.twitter_id
                voter_to_save = True
            # 'id_str': '132728535',
            # 'utc_offset': 32400,
            # 'description': "Cars, Musics, Games, Electronics, toys, food, etc... I'm just a typical boy!",
            # 'profile_image_url': 'http://a1.twimg.com/profile_images/1213351752/_2_2__normal.jpg',
            if positive_value_exists(cached_twitter_profile_image_url_https):
                voter.twitter_profile_image_url_https = cached_twitter_profile_image_url_https
                voter_to_save = True
            elif hasattr(twitter_auth_response, "twitter_profile_image_url_https") and \
                    positive_value_exists(twitter_auth_response.twitter_profile_image_url_https):
                voter.twitter_profile_image_url_https = twitter_auth_response.twitter_profile_image_url_https
                voter_to_save = True
            if positive_value_exists(we_vote_hosted_profile_image_url_large):
                voter.we_vote_hosted_profile_image_url_large = we_vote_hosted_profile_image_url_large
                voter_to_save = True
            if positive_value_exists(we_vote_hosted_profile_image_url_medium):
                voter.we_vote_hosted_profile_image_url_medium = we_vote_hosted_profile_image_url_medium
                voter_to_save = True
            if positive_value_exists(we_vote_hosted_profile_image_url_tiny):
                voter.we_vote_hosted_profile_image_url_tiny = we_vote_hosted_profile_image_url_tiny
                voter_to_save = True
            # 'profile_background_image_url': 'http://a2.twimg.com/a/1294785484/images/themes/theme15/bg.png',
            # 'screen_name': 'jaeeeee',
            if hasattr(twitter_auth_response, "twitter_screen_name") and \
                    positive_value_exists(twitter_auth_response.twitter_screen_name):
                voter.twitter_screen_name = twitter_auth_response.twitter_screen_name
                voter_to_save = True
            # 'lang': 'en',
            if hasattr(twitter_auth_response, "twitter_name") and \
                    positive_value_exists(twitter_auth_response.twitter_name):
                voter.twitter_name = twitter_auth_response.twitter_name
                voter_to_save = True
            # 'url': 'http://www.carbonize.co.kr',
            # 'time_zone': 'Seoul',
            if voter_to_save:
                voter.save()
            success = True
            status = "SAVED_VOTER_TWITTER_VALUES_FROM_TWITTER_AUTH_RESPONSE "
        except Exception as e:
            status = "UNABLE_TO_SAVE_VOTER_TWITTER_VALUES_FROM_TWITTER_AUTH_RESPONSE "
            success = False

        results = {
            'status':   status,
            'success':  success,
            'voter':    voter,
        }
        return results

    def save_twitter_user_values_from_dict(self, voter, twitter_user_dict,
                                           cached_twitter_profile_image_url_https=None,
                                           we_vote_hosted_profile_image_url_large=None,
                                           we_vote_hosted_profile_image_url_medium=None,
                                           we_vote_hosted_profile_image_url_tiny=None):
        try:
            # 'id': 132728535,
            if 'id' in twitter_user_dict:
                voter.twitter_id = twitter_user_dict['id']
            # 'id_str': '132728535',
            # 'utc_offset': 32400,
            # 'description': "Cars, Musics, Games, Electronics, toys, food, etc... I'm just a typical boy!",
            # 'profile_image_url': 'http://a1.twimg.com/profile_images/1213351752/_2_2__normal.jpg',
            if cached_twitter_profile_image_url_https:
                voter.twitter_profile_image_url_https = cached_twitter_profile_image_url_https
            elif 'profile_image_url_https' in twitter_user_dict:
                voter.twitter_profile_image_url_https = twitter_user_dict['profile_image_url_https']
            # 'profile_background_image_url': 'http://a2.twimg.com/a/1294785484/images/themes/theme15/bg.png',
            # 'screen_name': 'jaeeeee',
            if 'screen_name' in twitter_user_dict:
                voter.twitter_screen_name = twitter_user_dict['screen_name']
            if 'name' in twitter_user_dict:
                voter.twitter_name = twitter_user_dict['name']
            if we_vote_hosted_profile_image_url_large:
                voter.we_vote_hosted_profile_image_url_large = we_vote_hosted_profile_image_url_large
            if we_vote_hosted_profile_image_url_medium:
                voter.we_vote_hosted_profile_image_url_medium = we_vote_hosted_profile_image_url_medium
            if we_vote_hosted_profile_image_url_tiny:
                voter.we_vote_hosted_profile_image_url_tiny = we_vote_hosted_profile_image_url_tiny

            # 'lang': 'en',
            # 'name': 'Jae Jung Chung',
            # 'url': 'http://www.carbonize.co.kr',
            # 'time_zone': 'Seoul',
            voter.save()
            success = True
            status = "SAVED_VOTER_TWITTER_VALUES"
        except Exception as e:
            status = "UNABLE_TO_SAVE_VOTER_TWITTER_VALUES"
            success = False
            handle_record_not_saved_exception(e, logger=logger, exception_message_optional=status)

        results = {
            'status':   status,
            'success':  success,
            'voter':    voter,
        }
        return results

    def update_voter_twitter_details(self, twitter_id, twitter_json,
                                     cached_twitter_profile_image_url_https,
                                     we_vote_hosted_profile_image_url_large,
                                     we_vote_hosted_profile_image_url_medium,
                                     we_vote_hosted_profile_image_url_tiny):
        """
        Update existing voter entry with details retrieved from the Twitter API
        :param twitter_id:
        :param twitter_json:
        :param cached_twitter_profile_image_url_https:
        :param we_vote_hosted_profile_image_url_large:
        :param we_vote_hosted_profile_image_url_medium:
        :param we_vote_hosted_profile_image_url_tiny:
        :return:
        """
        voter_results = self.retrieve_voter_by_twitter_id(twitter_id)
        voter = voter_results['voter']
        if voter_results['voter_found']:
            # Twitter user already exists so update twitter user details
            results = self.save_twitter_user_values_from_dict(
                voter, twitter_json, cached_twitter_profile_image_url_https, we_vote_hosted_profile_image_url_large,
                we_vote_hosted_profile_image_url_medium,
                we_vote_hosted_profile_image_url_tiny)
        else:
            results = {
                'success':  False,
                'status':   'VOTER_NOT_FOUND',
                'voter':    voter
            }
        return results

    def update_voter_photos(self, voter_id, facebook_profile_image_url_https, facebook_photo_variable_exists):
        """
        Used by voterPhotoSave - this function is deprecated. Please do not extend.
        :param voter_id:
        :param facebook_profile_image_url_https:
        :param facebook_photo_variable_exists:
        :return:
        """
        results = self.retrieve_voter(voter_id)

        if results['voter_found']:
            voter = results['voter']

            try:
                if facebook_photo_variable_exists:
                    voter.facebook_profile_image_url_https = facebook_profile_image_url_https
                voter.save()
                status = "SAVED_VOTER_PHOTOS"
                success = True
            except Exception as e:
                status = "UNABLE_TO_SAVE_VOTER_PHOTOS"
                success = False
                handle_record_not_saved_exception(e, logger=logger, exception_message_optional=status)

        else:
            # If here, we were unable to find pre-existing Voter
            status = "UNABLE_TO_FIND_VOTER_FOR_UPDATE_VOTER_PHOTOS"
            voter = Voter()
            success = False

        results = {
            'status':   status,
            'success':  success,
            'voter':    voter,
        }
        return results

    def update_voter(self, voter_id, facebook_email, facebook_profile_image_url_https,
                     first_name, middle_name, last_name,
                     flag_integer_to_set, flag_integer_to_unset,
                     twitter_profile_image_url_https,
                     we_vote_hosted_profile_image_url_large=None,
                     we_vote_hosted_profile_image_url_medium=None, we_vote_hosted_profile_image_url_tiny=None):
        voter_updated = False
        results = self.retrieve_voter(voter_id)

        if results['voter_found']:
            voter = results['voter']

            try:
                should_save_voter = False
                if facebook_email is not False:
                    voter.facebook_email = facebook_email
                    should_save_voter = True
                if facebook_profile_image_url_https is not False:
                    voter.facebook_profile_image_url_https = facebook_profile_image_url_https
                    should_save_voter = True
                if first_name is not False:
                    voter.first_name = first_name
                    should_save_voter = True
                if middle_name is not False:
                    voter.middle_name = middle_name
                    should_save_voter = True
                if last_name is not False:
                    voter.last_name = last_name
                    should_save_voter = True
                if twitter_profile_image_url_https is not False:
                    voter.last_name = last_name
                    should_save_voter = True
                if positive_value_exists(we_vote_hosted_profile_image_url_large):
                    voter.we_vote_hosted_profile_image_url_large = we_vote_hosted_profile_image_url_large
                    should_save_voter = True
                if positive_value_exists(we_vote_hosted_profile_image_url_medium):
                    voter.we_vote_hosted_profile_image_url_medium = we_vote_hosted_profile_image_url_medium
                    should_save_voter = True
                if positive_value_exists(we_vote_hosted_profile_image_url_tiny):
                    voter.we_vote_hosted_profile_image_url_tiny = we_vote_hosted_profile_image_url_tiny
                    should_save_voter = True
                if flag_integer_to_set is not False:
                    voter.set_interface_status_flags(flag_integer_to_set)
                    should_save_voter = True
                if flag_integer_to_unset is not False:
                    voter.unset_interface_status_flags(flag_integer_to_unset)
                    should_save_voter = True
                if should_save_voter:
                    voter.save()
                    voter_updated = True
                status = "UPDATED_VOTER"
                success = True
            except Exception as e:
                status = "UNABLE_TO_UPDATE_VOTER"
                success = False
                voter_updated = False

        else:
            # If here, we were unable to find pre-existing Voter
            status = "UNABLE_TO_FIND_VOTER_FOR_UPDATE_VOTER"
            voter = Voter()
            success = False
            voter_updated = False

        results = {
            'status':                       status,
            'success':                      success,
            'voter':                        voter,
            'voter_updated':                voter_updated,
        }
        return results

    def update_voter_email_ownership_verified(self, voter, email_address_object):
        status = ""
        success = True  # Assume success unless we hit a problem
        voter_updated = False
        voter_manager = VoterManager()

        try:
            should_save_voter = False
            if email_address_object.email_ownership_is_verified:
                voter.primary_email_we_vote_id = email_address_object.we_vote_id
                voter.email = email_address_object.normalized_email_address
                voter.email_ownership_is_verified = True
                should_save_voter = True

            if should_save_voter:
                voter.save()
                voter_updated = True
            status += "UPDATED_VOTER_EMAIL_OWNERSHIP"
            success = True
        except Exception as e:
            status += "UNABLE_TO_UPDATE_INCOMING_VOTER "
            # We tried to update the incoming voter found but got an error, so we retrieve voter's based on
            #  normalized_email address, and then by primary_email_we_vote_id
            remove_cached_results = voter_manager.remove_voter_cached_email_entries_from_email_address_object(
                email_address_object)
            status += remove_cached_results['status']

            # And now, try to save again
            try:
                voter.primary_email_we_vote_id = email_address_object.we_vote_id
                voter.email = email_address_object.normalized_email_address
                voter.email_ownership_is_verified = True
                voter.save()
                voter_updated = True
                status += "UPDATED_VOTER_EMAIL_OWNERSHIP2 "
                success = True
            except Exception as e:
                success = False
                status += "UNABLE_TO_UPDATE_VOTER_EMAIL_OWNERSHIP2 "

        results = {
            'status': status,
            'success': success,
            'voter': voter,
            'voter_updated': voter_updated,
        }
        return results

    def update_voter_with_facebook_link_verified(self, voter, facebook_user_id, facebook_email):
        should_save_voter = False
        voter_updated = False

        try:
            voter.facebook_id = facebook_user_id
            voter.facebook_email = facebook_email
            should_save_voter = True

            if should_save_voter:
                voter.save()
                voter_updated = True
            status = "UPDATED_VOTER_WITH_FACEBOOK_LINK"
            success = True
        except Exception as e:
            status = "UNABLE_TO_UPDATE_VOTER_WITH_FACEBOOK_LINK"
            success = False
            voter_updated = False

        results = {
            'status': status,
            'success': success,
            'voter': voter,
            'voter_updated': voter_updated,
        }
        return results

    def update_voter_with_twitter_link_verified(self, voter, twitter_id):
        """
        I think this was originally built with the idea that we would cache the Twitter ID in the
        voter record for quick lookup. As of 2016-10-29 I don't think we can cache the twitter_id reliably
        because of the complexities of merging accounts and the chances for errors. So we should deprecate this.
        :param voter:
        :param twitter_id:
        :return:
        """
        should_save_voter = False
        voter_updated = False

        try:
            if positive_value_exists(twitter_id):
                voter.twitter_id = twitter_id
                should_save_voter = True

            if should_save_voter:
                voter.save()
                voter_updated = True
                status = "UPDATED_VOTER_WITH_TWITTER_LINK"
                success = True
            else:
                status = "NOT_UPDATED_VOTER_WITH_TWITTER_LINK"
                success = False
        except Exception as e:
            status = "UNABLE_TO_UPDATE_VOTER_WITH_TWITTER_LINK"
            success = False
            voter_updated = False

        results = {
            'status': status,
            'success': success,
            'voter': voter,
            'voter_updated': voter_updated,
        }
        return results


class Voter(AbstractBaseUser):
    """
    A fully featured User model with admin-compliant permissions that uses
    a full-length email field as the username.

    No fields are required, since at its very simplest, we only need the voter_id based on a voter_device_id.
    """
    alphanumeric = RegexValidator(r'^[0-9a-zA-Z]*$', message='Only alphanumeric characters are allowed.')

    # The we_vote_id identifier is unique across all We Vote sites, and allows us to share our voter info with other
    # organizations running the we_vote server
    # It starts with "wv" then we add on a database specific identifier like "3v" (WeVoteSetting.site_unique_id_prefix)
    # then the string "voter", and then a sequential integer like "123".
    # We keep the last value in WeVoteSetting.we_vote_id_last_org_integer
    we_vote_id = models.CharField(
        verbose_name="we vote permanent id", max_length=255, null=True, blank=True, unique=True)
    # When a person using an organization's Twitter handle signs in, we create a voter account. This is how
    #  we link the voter account to the organization.
    linked_organization_we_vote_id = models.CharField(
        verbose_name="we vote id for linked organization", max_length=255, null=True, blank=True, unique=True)

    # Redefine the basic fields that would normally be defined in User
    # username = models.CharField(unique=True, max_length=20, validators=[alphanumeric])  # Increase max_length to 255
    # We cache the email here for quick lookup, but the official email address for the voter
    # is referenced by primary_email_we_vote_id and stored in the EmailAddress table
    email = models.EmailField(verbose_name='email address', max_length=255, unique=True, null=True, blank=True)
    primary_email_we_vote_id = models.CharField(
        verbose_name="we vote id for primary email for this voter", max_length=255, null=True, blank=True, unique=True)
    # This "email_ownership_is_verified" is a copy of the master data in EmailAddress.email_ownership_is_verified
    email_ownership_is_verified = models.BooleanField(default=False)
    first_name = models.CharField(verbose_name='first name', max_length=255, null=True, blank=True)
    middle_name = models.CharField(max_length=255, null=True, blank=True)
    last_name = models.CharField(verbose_name='last name', max_length=255, null=True, blank=True)
    date_joined = models.DateTimeField(verbose_name='date joined', auto_now_add=True)
    date_last_changed = models.DateTimeField(verbose_name='date last changed', null=True, auto_now=True)

    is_active = models.BooleanField(default=True)
    is_admin = models.BooleanField(default=False)
    is_verified_volunteer = models.BooleanField(default=False)

    # Facebook session information
    facebook_id = models.BigIntegerField(verbose_name="facebook big integer id", null=True, blank=True)
    facebook_email = models.EmailField(verbose_name='facebook email address', max_length=255, unique=False,
                                       null=True, blank=True)
    fb_username = models.CharField(unique=True, max_length=20, validators=[alphanumeric], null=True)
    facebook_profile_image_url_https = models.URLField(verbose_name='url of image from facebook', blank=True, null=True)

    # Twitter session information
    twitter_id = models.BigIntegerField(verbose_name="twitter big integer id", null=True, blank=True)
    twitter_name = models.CharField(verbose_name="display name from twitter", max_length=255, null=True, blank=True)
    twitter_screen_name = models.CharField(verbose_name='twitter screen name / handle',
                                           max_length=255, null=True, unique=False)
    twitter_profile_image_url_https = models.URLField(verbose_name='url of logo from twitter', blank=True, null=True)
    we_vote_hosted_profile_image_url_large = models.URLField(verbose_name='we vote hosted large image url',
                                                             blank=True, null=True)
    we_vote_hosted_profile_image_url_medium = models.URLField(verbose_name='we vote hosted medium image url',
                                                              blank=True, null=True)
    we_vote_hosted_profile_image_url_tiny = models.URLField(verbose_name='we vote hosted tiny image url',
                                                            blank=True, null=True)

    twitter_request_token = models.TextField(verbose_name='twitter request token', null=True, blank=True)
    twitter_request_secret = models.TextField(verbose_name='twitter request secret', null=True, blank=True)
    twitter_access_token = models.TextField(verbose_name='twitter access token', null=True, blank=True)
    twitter_access_secret = models.TextField(verbose_name='twitter access secret', null=True, blank=True)
    twitter_connection_active = models.BooleanField(default=False)

    # Interface Status Flags is a positive integer, when represented as a stream of bits,
    # each bit maps to a status of a variable's boolean value
    # for eg. the first bit(rightmost bit) = 1 means, the SUPPORT_OPPOSE_MODAL_SHOWN_BIT has been shown
    # more constants at top of this file
    interface_status_flags = models.PositiveIntegerField(verbose_name="interface status flags", default=0)

    # Custom We Vote fields
#     image_displayed
#     image_twitter
#     image_facebook
#     blocked
#     flags (ex/ signed_in)
#     password_hashed
#     password_reset_key
#     password_reset_request_time
#     last_activity

    # The unique ID of the election this voter is currently looking at. (Provided by Google Civic)
    # DALE 2015-10-29 We are replacing this with looking up the value in the ballot_items table, and then
    # storing in cookie
    # current_google_civic_election_id = models.PositiveIntegerField(
    #     verbose_name="google civic election id", null=True, unique=False)

    objects = VoterManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []  # Since we need to store a voter based solely on voter_device_id, no values are required

    # We override the save function to allow for the email field to be empty. If NOT empty, email must be unique.
    # We also want to auto-generate we_vote_id
    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower().strip()  # Hopefully reduces junk to ""
            if self.email != "":  # If it's not blank
                if not validate_email(self.email):  # ...make sure it is a valid email
                    # If it isn't a valid email, don't save the value as an email -- just save a blank field
                    self.email = None
        if self.email == "":
            self.email = None
        if self.we_vote_id:
            self.we_vote_id = self.we_vote_id.strip().lower()
        if self.we_vote_id == "" or self.we_vote_id is None:  # If there isn't a value...
            # ...generate a new id
            site_unique_id_prefix = fetch_site_unique_id_prefix()
            next_local_integer = fetch_next_we_vote_id_last_voter_integer()
            # "wv" = We Vote
            # site_unique_id_prefix = a generated (or assigned) unique id for one server running We Vote
            # "voter" = tells us this is a unique id for an org
            # next_integer = a unique, sequential integer for this server - not necessarily tied to database id
            self.we_vote_id = "wv{site_unique_id_prefix}voter{next_integer}".format(
                site_unique_id_prefix=site_unique_id_prefix,
                next_integer=next_local_integer,
            )
            # TODO we need to deal with the situation where we_vote_id is NOT unique on save
        super(Voter, self).save(*args, **kwargs)

    def get_full_name(self):
        full_name = self.first_name if positive_value_exists(self.first_name) else ''
        full_name += " " if positive_value_exists(self.first_name) and positive_value_exists(self.last_name) else ''
        full_name += self.last_name if positive_value_exists(self.last_name) else ''

        if not positive_value_exists(full_name):
            if positive_value_exists(self.twitter_name):
                full_name = self.twitter_name
            else:
                full_name = self.twitter_screen_name

        if not positive_value_exists(full_name) and positive_value_exists(self.email):
            full_name = self.email.split("@", 1)[0]

        if not positive_value_exists(full_name):
            full_name = "Voter-" + self.we_vote_id

        return full_name

    def get_short_name(self):
        # return self.first_name
        # The user is identified by their email address
        return self.email

    def voter_can_retrieve_account(self):
        if positive_value_exists(self.email):
            return True
        else:
            return False

    def __str__(self):              # __unicode__ on Python 2
        if self.has_valid_email():
            return str(self.email)
        elif positive_value_exists(self.twitter_screen_name):
            return str(self.twitter_screen_name)
        else:
            return str(self.get_full_name())

    def has_perm(self, perm, obj=None):
        """
        Does the user have a specific permission?
        """
        # Simplest possible answer: Yes, always
        return True

    def has_module_perms(self, app_label):
        """
        Does the user have permissions to view the app `app_label`?
        """
        # Simplest possible answer: Yes, always
        return True

    @property
    def is_staff(self):
        """
        Is the user a member of staff?
        """
        # Simplest possible answer: All admins are staff
        return self.is_admin

    def voter_photo_url(self):
        # If we have facebook id, we might want to use this:
        # https://graph.facebook.com/504209299/picture?type=square
        if self.facebook_profile_image_url_https:
            return self.facebook_profile_image_url_https
        elif self.twitter_profile_image_url_https:
            return self.twitter_profile_image_url_https
        return ''

    def is_signed_in(self):
        if self.signed_in_with_email() or self.signed_in_facebook() or self.signed_in_twitter():
            return True
        return False

    def signed_in_facebook(self):
        facebook_manager = FacebookManager()
        facebook_link_results = facebook_manager.retrieve_facebook_link_to_voter(0, self.we_vote_id)
        if facebook_link_results['facebook_link_to_voter_found']:
            facebook_link_to_voter = facebook_link_results['facebook_link_to_voter']
            if positive_value_exists(facebook_link_to_voter.facebook_user_id):
                return True
        return False

    def signed_in_google(self):
        return False

    def signed_in_twitter(self):
        twitter_user_manager = TwitterUserManager()
        twitter_link_results = twitter_user_manager.retrieve_twitter_link_to_voter(0, self.we_vote_id)
        if twitter_link_results['twitter_link_to_voter_found']:
            twitter_link_to_voter = twitter_link_results['twitter_link_to_voter']
            if positive_value_exists(twitter_link_to_voter.twitter_id):
                return True
        return False

    def signed_in_with_email(self):
        # TODO DALE Consider merging with has_email_with_verified_ownership
        verified_email_found = (positive_value_exists(self.email) or
                                positive_value_exists(self.primary_email_we_vote_id)) and \
                               self.email_ownership_is_verified
        if verified_email_found:
            return True
        return False

    def has_valid_email(self):
        if self.has_email_with_verified_ownership():
            return True
        return False

    def has_data_to_preserve(self):
        # Does this voter record have any values associated in this table that are unique
        if self.has_email_with_verified_ownership() or self.signed_in_twitter() or self.signed_in_facebook():
            return True
        else:
            # Has any important data been stored in other tables attached to this voter account?
            # (Each additional query costs more server resources, so we return True as early as we can.)
            # NOTE: We can't do this because we can't bring position classes in this file
            # Consider caching "has_position" data in the voter table
            # position_list_manager = PositionListManager()
            # positions_found = position_list_manager.positions_exist_for_voter(self.we_vote_id)
            # if positive_value_exists(positions_found):
            #     return True

            # Following any organizations?

            # No need to check for friends, because you can't have any without a signed in status, which we've checked
            # return True  # TODO DALE Set to True for testing
            pass

        return False

    def has_email_with_verified_ownership(self):
        # TODO DALE Consider merging with signed_in_with_email
        # Because there might be some cases where we can't update the email because of caching issues
        # (voter.email must be unique, and there was a bug where we tried to wipe out voter.email by setting
        # it to "", which failed), we only require email_ownership_is_verified to be true
        # if positive_value_exists(self.email) and self.email_ownership_is_verified:
        if self.email_ownership_is_verified:
            return True
        return False

    # for every bit set in flag_integer_to_set,
    # corresponding bits in self.interface_status_flags will be set
    def set_interface_status_flags(self, flag_integer_to_set):
        self.interface_status_flags = flag_integer_to_set | self.interface_status_flags

    # for every bit set in flag_integer_to_unset,
    # corresponding bits in self.interface_status_flags will be unset
    def unset_interface_status_flags(self, flag_integer_to_unset):
        self.interface_status_flags = ~flag_integer_to_unset & self.interface_status_flags


class VoterDeviceLink(models.Model):
    """
    There can be many voter_device_id's for every voter_id. (See commentary in class VoterDeviceLinkManager)
    """
    # The id for this object is not used in any searches
    # A randomly generated identifier that gets stored as a cookie on a single device
    # See wevote_functions.functions, function generate_voter_device_id for a discussion of voter_device_id length
    voter_device_id = models.CharField(verbose_name='voter device id',
                                       max_length=255, null=False, blank=False, unique=True)
    # The voter_id associated with voter_device_id
    voter_id = models.BigIntegerField(verbose_name="voter unique identifier", null=False, blank=False, unique=False)

    # The unique ID of the election (provided by Google Civic) that the voter is looking at on this device
    google_civic_election_id = models.PositiveIntegerField(
        verbose_name="google civic election id", default=0, null=False)

    def generate_voter_device_id(self):
        # A simple mapping to this function
        return generate_voter_device_id()


class VoterDeviceLinkManager(models.Model):
    """
    In order to start gathering information about a voter prior to authentication, we use a long randomized string
    stored as a browser cookie. As soon as we get any other identifiable information from a voter (like an email
    address), we capture that so the Voter record can be portable among devices. Note that any voter might be using
    We Vote from different browsers. The VoterDeviceLink links one or more voter_device_id's to one voter_id.

    Since (prior to authentication) every voter_device_id will have its own voter_id record, we merge and delete Voter
    records whenever we can.
    """

    def __str__(self):              # __unicode__ on Python 2
        return "Voter Device Id Manager"

    def delete_all_voter_device_links(self, voter_device_id):
        voter_id = fetch_voter_id_from_voter_device_link(voter_device_id)

        try:
            if positive_value_exists(voter_id):
                VoterDeviceLink.objects.filter(voter_id=voter_id).delete()
                status = "DELETE_ALL_VOTER_DEVICE_LINKS_SUCCESSFUL"
                success = True
            else:
                status = "DELETE_ALL_VOTER_DEVICE_LINKS-MISSING_VARIABLES"
                success = False
        except Exception as e:
            status = "DELETE_ALL_VOTER_DEVICE_LINKS-DATABASE_DELETE_EXCEPTION"
            success = False

        results = {
            'success':  success,
            'status':   status,
        }
        return results

    def delete_voter_device_link(self, voter_device_id):
        try:
            if positive_value_exists(voter_device_id):
                VoterDeviceLink.objects.filter(voter_device_id=voter_device_id).delete()
                status = "DELETE_VOTER_DEVICE_LINK_SUCCESSFUL"
                success = True
            else:
                status = "DELETE_VOTER_DEVICE_LINK-MISSING_VARIABLES"
                success = False
        except Exception as e:
            status = "DELETE_VOTER_DEVICE_LINK-DATABASE_DELETE_EXCEPTION"
            success = False

        results = {
            'success':  success,
            'status':   status,
        }
        return results

    def retrieve_voter_device_link_from_voter_device_id(self, voter_device_id):
        voter_id = 0
        voter_device_link_id = 0
        voter_device_link_manager = VoterDeviceLinkManager()
        results = voter_device_link_manager.retrieve_voter_device_link(voter_device_id, voter_id, voter_device_link_id)

        return results

    def retrieve_voter_device_link(self, voter_device_id, voter_id=0, voter_device_link_id=0):
        error_result = False
        exception_does_not_exist = False
        exception_multiple_object_returned = False
        status = ""
        voter_device_link_on_stage = VoterDeviceLink()

        try:
            if positive_value_exists(voter_device_id):
                status += " RETRIEVE_VOTER_DEVICE_LINK-GET_BY_VOTER_DEVICE_ID"
                voter_device_link_on_stage = VoterDeviceLink.objects.get(voter_device_id=voter_device_id)
                voter_device_link_id = voter_device_link_on_stage.id
            elif positive_value_exists(voter_id):
                status += " RETRIEVE_VOTER_DEVICE_LINK-GET_BY_VOTER_ID"
                voter_device_link_on_stage = VoterDeviceLink.objects.get(voter_id=voter_id)
                # If still here, we found an existing position
                voter_device_link_id = voter_device_link_on_stage.id
            elif positive_value_exists(voter_device_link_id):
                status += " RETRIEVE_VOTER_DEVICE_LINK-GET_BY_VOTER_DEVICE_LINK_ID"
                voter_device_link_on_stage = VoterDeviceLink.objects.get(id=voter_device_link_id)
                # If still here, we found an existing position
                voter_device_link_id = voter_device_link_on_stage.id
            else:
                voter_device_link_id = 0
                status += " RETRIEVE_VOTER_DEVICE_LINK-MISSING_REQUIRED_SEARCH_VARIABLES"
        except VoterDeviceLink.MultipleObjectsReturned as e:
            handle_record_found_more_than_one_exception(e, logger=logger)
            error_result = True
            exception_multiple_object_returned = True
            status += " RETRIEVE_VOTER_DEVICE_LINK-MULTIPLE_OBJECTS_RETURNED"
        except VoterDeviceLink.DoesNotExist:
            error_result = True
            exception_does_not_exist = True
            status += " RETRIEVE_VOTER_DEVICE_LINK-DOES_NOT_EXIST"

        results = {
            'success':                      True if not error_result else False,
            'status':                       status,
            'error_result':                 error_result,
            'DoesNotExist':                 exception_does_not_exist,
            'MultipleObjectsReturned':      exception_multiple_object_returned,
            'voter_device_link_found':      True if voter_device_link_id > 0 else False,
            'voter_device_link':            voter_device_link_on_stage,
        }
        return results

    def save_new_voter_device_link(self, voter_device_id, voter_id):
        error_result = False
        exception_record_not_saved = False
        missing_required_variables = False
        voter_device_link_on_stage = VoterDeviceLink()
        voter_device_link_id = 0

        try:
            if positive_value_exists(voter_device_id) and positive_value_exists(voter_id):
                voter_device_link_on_stage.voter_device_id = voter_device_id
                voter_device_link_on_stage.voter_id = voter_id
                voter_device_link_on_stage.save()

                voter_device_link_id = voter_device_link_on_stage.id
            else:
                missing_required_variables = True
                voter_device_link_id = 0
        except Exception as e:
            handle_record_not_saved_exception(e, logger=logger)
            error_result = True
            exception_record_not_saved = True

        results = {
            'error_result':                 error_result,
            'missing_required_variables':   missing_required_variables,
            'RecordNotSaved':               exception_record_not_saved,
            'voter_device_link_created':    True if voter_device_link_id > 0 else False,
            'voter_device_link':            voter_device_link_on_stage,
        }
        return results

    def update_voter_device_link_with_election_id(self, voter_device_link, google_civic_election_id):
        voter_object = None
        return self.update_voter_device_link(voter_device_link, voter_object, google_civic_election_id)

    def update_voter_device_link(self, voter_device_link, voter_object=None, google_civic_election_id=0):
        """
        Update existing voter_device_link with a new voter_id or google_civic_election_id
        """
        error_result = False
        exception_record_not_saved = False
        missing_required_variables = False
        voter_device_link_id = 0

        try:
            if positive_value_exists(voter_device_link.voter_device_id):
                if voter_object and positive_value_exists(voter_object.id):
                    voter_device_link.voter_id = voter_object.id
                if positive_value_exists(google_civic_election_id):
                    voter_device_link.google_civic_election_id = google_civic_election_id
                elif google_civic_election_id == 0:
                    # If set literally to 0, save it
                    voter_device_link.google_civic_election_id = 0
                voter_device_link.save()

                voter_device_link_id = voter_device_link.id
            else:
                missing_required_variables = True
                voter_device_link_id = 0
        except Exception as e:
            handle_record_not_saved_exception(e, logger=logger)
            error_result = True
            exception_record_not_saved = True

        results = {
            'error_result':                 error_result,
            'missing_required_variables':   missing_required_variables,
            'RecordNotSaved':               exception_record_not_saved,
            'voter_device_link_updated':    True if voter_device_link_id > 0 else False,
            'voter_device_link':            voter_device_link,
        }
        return results


# This method *just* returns the voter_id or 0
def fetch_voter_id_from_voter_device_link(voter_device_id):
    voter_device_link_manager = VoterDeviceLinkManager()
    results = voter_device_link_manager.retrieve_voter_device_link_from_voter_device_id(voter_device_id)
    if results['voter_device_link_found']:
        voter_device_link = results['voter_device_link']
        return voter_device_link.voter_id
    return 0


# This method *just* returns the voter_id or 0
def fetch_voter_id_from_voter_we_vote_id(we_vote_id):
    voter_manager = VoterManager()
    results = voter_manager.retrieve_voter_by_we_vote_id(we_vote_id)
    if results['voter_found']:
        voter = results['voter']
        return voter.id
    return 0


# This method *just* returns the voter_we_vote_id or ""
def fetch_voter_we_vote_id_from_voter_id(voter_id):
    voter_manager = VoterManager()
    results = voter_manager.retrieve_voter_by_id(voter_id)
    if results['voter_found']:
        voter = results['voter']
        return voter.we_vote_id
    return ""

def fetch_voter_we_vote_id_from_voter_device_link(voter_device_id):
    voter_device_link_manager = VoterDeviceLinkManager()
    results = voter_device_link_manager.retrieve_voter_device_link_from_voter_device_id(voter_device_id)
    if results['voter_device_link_found']:
        voter_device_link = results['voter_device_link']
        voter_id = voter_device_link.voter_id
        voter_manager = VoterManager()
        results = voter_manager.retrieve_voter_by_id(voter_id)
        if results['voter_found']:
            voter = results['voter']
            return voter.we_vote_id
        return ""

def retrieve_voter_authority(request):
    voter_api_device_id = get_voter_api_device_id(request)
    voter_manager = VoterManager()
    results = voter_manager.retrieve_voter_from_voter_device_id(voter_api_device_id)
    if results['voter_found']:
        voter = results['voter']
        authority_results = {
            'voter_found':              True,
            'is_active':                positive_value_exists(voter.is_active),
            'is_admin':                 positive_value_exists(voter.is_admin),
            'is_verified_volunteer':    positive_value_exists(voter.is_verified_volunteer),
        }
        return authority_results

    authority_results = {
        'voter_found':              False,
        'is_active':                False,
        'is_admin':                 False,
        'is_verified_volunteer':    False,
    }
    return authority_results


def voter_has_authority(request, authority_required, authority_results=None):
    if not authority_results:
        authority_results = retrieve_voter_authority(request)
    if not positive_value_exists(authority_results['is_active']):
        return False
    if 'admin' in authority_required:
        if positive_value_exists(authority_results['is_admin']):
            return True
    if 'verified_volunteer' in authority_required:
        if positive_value_exists(authority_results['is_verified_volunteer']) or \
                positive_value_exists(authority_results['is_admin']):
            return True
    return False

# class VoterJurisdictionLink(models.Model):
#     """
#     All of the jurisdictions the Voter is in
#     """
#     voter = models.ForeignKey(Voter, null=False, blank=False, verbose_name='voter')
#     jurisdiction = models.ForeignKey(Jurisdiction,
#                                      null=False, blank=False, verbose_name="jurisdiction this voter votes in")

BALLOT_ADDRESS = 'B'
MAILING_ADDRESS = 'M'
FORMER_BALLOT_ADDRESS = 'F'
ADDRESS_TYPE_CHOICES = (
    (BALLOT_ADDRESS, 'Address Where Registered to Vote'),
    (MAILING_ADDRESS, 'Mailing Address'),
    (FORMER_BALLOT_ADDRESS, 'Prior Address'),
)


class VoterAddress(models.Model):
    """
    An address of a registered voter for ballot purposes.
    """
    #
    # We are relying on built-in Python id field

    # The voter_id that owns this address
    voter_id = models.BigIntegerField(verbose_name="voter unique identifier", null=False, blank=False, unique=False)
    address_type = models.CharField(
        verbose_name="type of address", max_length=1, choices=ADDRESS_TYPE_CHOICES, default=BALLOT_ADDRESS)

    text_for_map_search = models.CharField(max_length=255, blank=False, null=False, verbose_name='address as entered')

    latitude = models.CharField(max_length=255, blank=True, null=True, verbose_name='latitude returned from Google')
    longitude = models.CharField(max_length=255, blank=True, null=True, verbose_name='longitude returned from Google')
    normalized_line1 = models.CharField(max_length=255, blank=True, null=True,
                                        verbose_name='normalized address line 1 returned from Google')
    normalized_line2 = models.CharField(max_length=255, blank=True, null=True,
                                        verbose_name='normalized address line 2 returned from Google')
    normalized_city = models.CharField(max_length=255, blank=True, null=True,
                                       verbose_name='normalized city returned from Google')
    normalized_state = models.CharField(max_length=255, blank=True, null=True,
                                        verbose_name='normalized state returned from Google')
    normalized_zip = models.CharField(max_length=255, blank=True, null=True,
                                      verbose_name='normalized zip returned from Google')
    # This is the election_id last found for this address
    google_civic_election_id = models.PositiveIntegerField(
        verbose_name="google civic election id for this address", null=True, unique=False)
    # The last election day this address was used to retrieve a ballot
    election_day_text = models.CharField(verbose_name="election day", max_length=255, null=True, blank=True)

    refreshed_from_google = models.BooleanField(
        verbose_name="have normalized fields been updated from Google since address change?", default=False)


class VoterAddressManager(models.Model):

    def __unicode__(self):
        return "VoterAddressManager"

    def retrieve_address(self, voter_address_id, voter_id=0, address_type=''):
        error_result = False
        exception_does_not_exist = False
        exception_multiple_object_returned = False
        voter_address_on_stage = VoterAddress()
        voter_address_has_value = False

        if not positive_value_exists(address_type):
            # Provide a default
            address_type = BALLOT_ADDRESS

        try:
            if positive_value_exists(voter_address_id):
                voter_address_on_stage = VoterAddress.objects.get(id=voter_address_id)
                voter_address_id = voter_address_on_stage.id
                voter_address_found = True
                status = "VOTER_ADDRESS_FOUND_BY_ID"
                success = True
                voter_address_has_value = True if positive_value_exists(voter_address_on_stage.text_for_map_search) \
                    else False
            elif positive_value_exists(voter_id) and address_type in (BALLOT_ADDRESS, MAILING_ADDRESS,
                                                                      FORMER_BALLOT_ADDRESS):
                voter_address_on_stage = VoterAddress.objects.get(voter_id=voter_id, address_type=address_type)
                # If still here, we found an existing address
                voter_address_id = voter_address_on_stage.id
                voter_address_found = True
                status = "VOTER_ADDRESS_FOUND_BY_VOTER_ID_AND_ADDRESS_TYPE"
                success = True
                voter_address_has_value = True if positive_value_exists(voter_address_on_stage.text_for_map_search) \
                    else False
            else:
                voter_address_found = False
                status = "VOTER_ADDRESS_NOT_FOUND-MISSING_REQUIRED_VARIABLES"
                success = False
        except VoterAddress.MultipleObjectsReturned as e:
            handle_record_found_more_than_one_exception(e, logger=logger)
            error_result = True
            status = "VOTER_ADDRESS_MULTIPLE_OBJECTS_RETURNED"
            exception_multiple_object_returned = True
            success = False
            voter_address_found = False
        except VoterAddress.DoesNotExist:
            error_result = True
            status = "VOTER_ADDRESS_DOES_NOT_EXIST"
            exception_does_not_exist = True
            success = True
            voter_address_found = False

        results = {
            'success':                  success,
            'status':                   status,
            'error_result':             error_result,
            'DoesNotExist':             exception_does_not_exist,
            'MultipleObjectsReturned':  exception_multiple_object_returned,
            'voter_address_found':      voter_address_found,
            'voter_address_has_value':  voter_address_has_value,
            'voter_address_id':         voter_address_id,
            'voter_address':            voter_address_on_stage,
        }
        return results

    def retrieve_ballot_address_from_voter_id(self, voter_id):
        voter_address_id = 0
        address_type = BALLOT_ADDRESS
        voter_address_manager = VoterAddressManager()
        return voter_address_manager.retrieve_address(voter_address_id, voter_id, address_type)

    def retrieve_ballot_map_text_from_voter_id(self, voter_id):
        results = self.retrieve_ballot_address_from_voter_id(voter_id)

        ballot_map_text = ''
        if results['voter_address_found']:
            voter_address = results['voter_address']
            minimum_normalized_address_data_exists = positive_value_exists(
                voter_address.normalized_city) or positive_value_exists(
                    voter_address.normalized_state) or positive_value_exists(voter_address.normalized_zip)
            if minimum_normalized_address_data_exists:
                ballot_map_text += voter_address.normalized_line1 \
                    if positive_value_exists(voter_address.normalized_line1) else ''
                ballot_map_text += ", " \
                    if positive_value_exists(voter_address.normalized_line1) \
                    and positive_value_exists(voter_address.normalized_city) \
                    else ''
                ballot_map_text += voter_address.normalized_city \
                    if positive_value_exists(voter_address.normalized_city) else ''
                ballot_map_text += ", " \
                    if positive_value_exists(voter_address.normalized_city) \
                    and positive_value_exists(voter_address.normalized_state) \
                    else ''
                ballot_map_text += voter_address.normalized_state \
                    if positive_value_exists(voter_address.normalized_state) else ''
                ballot_map_text += " " + voter_address.normalized_zip \
                    if positive_value_exists(voter_address.normalized_zip) else ''
            elif positive_value_exists(voter_address.text_for_map_search):
                ballot_map_text += voter_address.text_for_map_search
        return ballot_map_text

    # # TODO TEST THIS
    # def retrieve_addresses(self, voter_id):
    #     error_result = False
    #     exception_does_not_exist = False
    #     # voter_addresses_on_stage = # How to typecast?
    #     number_of_addresses = 0
    #
    #     try:
    #         if voter_id > 0:
    #             voter_addresses_on_stage = VoterAddress.objects.get(voter_id=voter_id)
    #             number_of_addresses = len(voter_addresses_on_stage)
    #     except VoterAddress.DoesNotExist:
    #         error_result = True
    #         exception_does_not_exist = True
    #
    #     results = {
    #         'error_result':             error_result,
    #         'DoesNotExist':             exception_does_not_exist,
    #         'voter_addresses_found':    True if number_of_addresses > 0 else False,
    #         'voter_addresses_on_stage': voter_addresses_on_stage,
    #         'number_of_addresses':      number_of_addresses,
    #     }
    #     return results

    def retrieve_text_for_map_search_from_voter_id(self, voter_id):
        results = self.retrieve_ballot_address_from_voter_id(voter_id)

        text_for_map_search = ''
        if results['voter_address_found']:
            voter_address = results['voter_address']
            text_for_map_search = voter_address.text_for_map_search
        return text_for_map_search

    def update_or_create_voter_address(self, voter_id, address_type, raw_address_text):
        """
        NOTE: This approach won't support multiple FORMER_BALLOT_ADDRESS
        :param voter_id:
        :param address_type:
        :param raw_address_text:
        :return:
        """
        status = ''
        exception_multiple_object_returned = False
        new_address_created = False
        voter_address_on_stage = None
        voter_address_on_stage_found = False

        if positive_value_exists(voter_id) and address_type in (BALLOT_ADDRESS, MAILING_ADDRESS, FORMER_BALLOT_ADDRESS):
            try:
                updated_values = {
                    # Values we search against
                    'voter_id': voter_id,
                    'address_type': address_type,
                    # The rest of the values are to be saved
                    'text_for_map_search':      raw_address_text,
                    'latitude':                 None,
                    'longitude':                None,
                    'normalized_line1':         None,
                    'normalized_line2':         None,
                    'normalized_city':          None,
                    'normalized_state':         None,
                    'normalized_zip':           None,
                    # We clear out former values for these so voter_ballot_items_retrieve_for_api resets them
                    'refreshed_from_google':    False,
                    'google_civic_election_id': 0,
                    'election_day_text':        '',
                }

                voter_address_on_stage, new_address_created = VoterAddress.objects.update_or_create(
                    voter_id__exact=voter_id, address_type=address_type, defaults=updated_values)
                voter_address_on_stage_found = voter_address_on_stage.id
                success = True
            except VoterAddress.MultipleObjectsReturned as e:
                handle_record_found_more_than_one_exception(e, logger=logger)
                success = False
                status = 'MULTIPLE_MATCHING_ADDRESSES_FOUND'
                exception_multiple_object_returned = True
        else:
            success = False
            status = 'MISSING_VOTER_ID_OR_ADDRESS_TYPE'

        results = {
            'success':                  success,
            'status':                   status,
            'MultipleObjectsReturned':  exception_multiple_object_returned,
            'voter_address_saved':      success,
            'address_type':             address_type,
            'new_address_created':      new_address_created,
            'voter_address_found':      voter_address_on_stage_found,
            'voter_address':            voter_address_on_stage,
        }
        return results

    def update_voter_address_with_normalized_values(self, voter_id, voter_address_dict):
        voter_address_id = 0
        address_type = BALLOT_ADDRESS
        results = self.retrieve_address(voter_address_id, voter_id, address_type)

        if results['success']:
            voter_address = results['voter_address']

            try:
                voter_address.normalized_line1 = voter_address_dict['line1']
                voter_address.normalized_city = voter_address_dict['city']
                voter_address.normalized_state = voter_address_dict['state']
                voter_address.normalized_zip = voter_address_dict['zip']
                voter_address.refreshed_from_google = True
                voter_address.save()
                status = "SAVED_VOTER_ADDRESS_WITH_NORMALIZED_VALUES"
                success = True
            except Exception as e:
                status = "UNABLE_TO_SAVE_VOTER_ADDRESS_WITH_NORMALIZED_VALUES"
                success = False
                handle_record_not_saved_exception(e, logger=logger, exception_message_optional=status)

        else:
            # If here, we were unable to find pre-existing VoterAddress
            status = "UNABLE_TO_FIND_VOTER_ADDRESS"
            voter_address = VoterAddress()  # TODO Finish this for "create new" case
            success = False

        results = {
            'status':   status,
            'success':  success,
            'voter_address': voter_address,
        }
        return results

    def update_existing_voter_address_object(self, voter_address_object):
        results = self.retrieve_address(voter_address_object.id)

        if results['success']:
            try:
                voter_address_object.save()  # Save the incoming object
                status = "UPDATED_EXISTING_VOTER_ADDRESS"
                success = True
                voter_address_found = True
            except Exception as e:
                status = "UNABLE_TO_UPDATE_EXISTING_VOTER_ADDRESS"
                success = False
                voter_address_found = False
                handle_record_not_saved_exception(e, logger=logger, exception_message_optional=status)
        else:
            # If here, we were unable to find pre-existing VoterAddress
            status = "UNABLE_TO_FIND_AND_UPDATE_VOTER_ADDRESS"
            voter_address_object = None
            success = False
            voter_address_found = False

        results = {
            'status':               status,
            'success':              success,
            'voter_address':        voter_address_object,
            'voter_address_found':  voter_address_found,
        }
        return results


def voter_setup(request):
    """
    This is only used for sign in on the API server, and is not used for WebApp
    :param request:
    :return:
    """
    generate_voter_api_device_id_if_needed = True
    voter_api_device_id = get_voter_api_device_id(request, generate_voter_api_device_id_if_needed)

    voter_id = 0
    voter_id_found = False
    store_new_voter_api_device_id_in_cookie = True

    voter_device_link_manager = VoterDeviceLinkManager()
    results = voter_device_link_manager.retrieve_voter_device_link_from_voter_device_id(voter_api_device_id)
    if results['voter_device_link_found']:
        voter_device_link = results['voter_device_link']
        voter_id = voter_device_link.voter_id
        voter_id_found = True if positive_value_exists(voter_id) else False
        store_new_voter_api_device_id_in_cookie = False if positive_value_exists(voter_id_found) else True

    # If existing voter not found, create a new voter
    if not voter_id_found:
        # Create a new voter and return the id
        voter_manager = VoterManager()
        results = voter_manager.create_voter()

        if results['voter_created']:
            voter = results['voter']
            voter_id = voter.id

            # Now save the voter_device_link
            results = voter_device_link_manager.save_new_voter_device_link(voter_api_device_id, voter_id)

            if results['voter_device_link_created']:
                voter_device_link = results['voter_device_link']
                voter_id = voter_device_link.voter_id
                voter_id_found = True if voter_id > 0 else False
                store_new_voter_api_device_id_in_cookie = True
            else:
                voter_id = 0
                voter_id_found = False

    final_results = {
        'voter_id':                                 voter_id,
        'voter_api_device_id':                      voter_api_device_id,
        'voter_id_found':                           voter_id_found,
        'store_new_voter_api_device_id_in_cookie':  store_new_voter_api_device_id_in_cookie,
    }
    return final_results
