#
# This class is intended to make cobbler profile rhel63-x86_64.
#
# [distro] The name of cobbler distro to bind profile to.
#
# [ks_repo] Repository definitions (array of hashes with name and url keys)
# where RPM packages are available which are not available in the main
# kickstart url.
#
# [ks_system_timezone] System timezone on installed system.
#
# [ks_encrypted_root_password] Hash of the root password on installed system.

class cobbler::profile::rhel63-x86_64(
  $distro  = "rhel63-x86_64",
  $ks_repo = [
              {
              "name" => "Puppet",
              "url"  => "http://yum.puppetlabs.com/el/6/products/x86_64",
              }],
              
  $ks_system_timezone         = "America/Los_Angeles",

  # default password is 'r00tme'
  $ks_encrypted_root_password = "\$6\$tCD3X7ji\$1urw6qEMDkVxOkD33b4TpQAjRiCeDZx0jmgMhDYhfB9KuGfqO9OcMaKyUxnGGWslEDQ4HxTw7vcAMP85NxQe61",
  ) {

  Exec {path => '/usr/bin:/bin:/usr/sbin:/sbin'}

  case $operatingsystem {
    /(?i)(ubuntu|debian|centos|redhat)$/:  {
      $ks_dir = "/var/lib/cobbler/kickstarts"
    }
  }
  
  file { "${ks_dir}/rhel63-x86_64.ks":
    content => template("cobbler/rhel.ks.erb"),
    owner => root,
    group => root,
    mode => 0644,
  } ->

  cobbler_profile { "rhel63-x86_64":
    kickstart => "${ks_dir}/rhel63-x86_64.ks",
    kopts => $kopts,
    distro => $distro,
    ksmeta => "",
    menu => true,
  }
  
}

