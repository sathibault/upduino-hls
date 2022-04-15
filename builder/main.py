import os
import re
import glob

from platform import system
import SCons
from SCons.Script import COMMAND_LINE_TARGETS, AlwaysBuild, Default, DefaultEnvironment, SConscript

def find_radiant():
    pgrdir = None
    for filename in ['/lscc/programmer']:
        testdir = os.path.join(filename, 'radiant')
        if os.path.isdir(testdir):
            pgrdir = os.path.normpath(testdir)
    if not pgrdir:
        for filename in glob.glob('/lscc/radiant/*'):
            testdir = os.path.join(filename, 'programmer')
            if os.path.isdir(testdir):
                pgrdir = os.path.normpath(testdir)
    if not pgrdir:
        raise Exception('Cannot find Radiant programmer')
    exes = glob.glob(os.path.join(pgrdir, '**/pgrcmd.exe'), recursive=True)
    if len(exes) == 0:
        raise Exception('Cannot find Radiant programmer executable')
    return(exes[0])

def xcf_generator(target, source, env):
    filename = env.subst(os.path.join(os.path.join('$BUILD_DIR', 'hw'), 'upduino.xcf'))
    pat = re.compile(r'<File>[^<]*</File>')
    with open(filename) as fd:
        xcf = fd.read()
        fullpath = env.subst(os.path.join('$PROJECT_DIR', str(source[0])))
        loc = '<File>'+fullpath+'</File>'
        xcf = pat.sub(str(loc).replace('\\', r'\\'), xcf)
        outf = open(str(target[0]), 'w')
        outf.write(xcf)
        outf.close()
    return None

env = DefaultEnvironment()
pio = env.PioPlatform()

inst_dir = pio.get_package_dir('toolchain-logicc')

env.Replace(LOGICC_DIR=inst_dir)

if system() == 'Windows':
    try:
        gccdir = pio.get_package_dir('toolchain-gccmingw32')
    except KeyError:
        raise SCons.Errors.UserError('ERROR: Please install windows_x86')

    if gccdir == None:
        raise SCons.Errors.UserError('ERROR: Please install windows_x86')

    env.Replace(
        _BINPREFIX="",
        AR="${_BINPREFIX}ar",
        AS="${_BINPREFIX}as",
        CC="${_BINPREFIX}gcc",
        CXX="${_BINPREFIX}g++",
        GDB="${_BINPREFIX}gdb",
        OBJCOPY="${_BINPREFIX}objcopy",
        RANLIB="${_BINPREFIX}ranlib",
        SIZETOOL="${_BINPREFIX}size",

        SIZEPRINTCMD='$SIZETOOL $SOURCES',
        PROGSUFFIX=".exe"
    )

    env.Append(
        CXXFLAGS=[
            "--std=c++14"
        ],
        CPPPATH=[
            os.path.join(inst_dir,'include')
        ],
        LINKFLAGS=[
            "-static",
            "-static-libgcc",
            "-static-libstdc++"
        ]
    )
else:
    # Remove generic C/C++ tools
    for k in ("CC", "CXX"):
        if k in env:
            del env[k]

    # Preserve C and C++ build flags
    backup_cflags = env.get("CFLAGS", [])
    backup_cxxflags = env.get("CXXFLAGS", [])

    # Scan for GCC compiler
    env.Tool("gcc")
    env.Tool("g++")

    # Reload "compilation_db" tool
    if "compiledb" in COMMAND_LINE_TARGETS:
        env.Tool("compilation_db")

    # Restore C/C++ build flags as they were overridden by env.Tool
    env.Append(CFLAGS=backup_cflags, CXXFLAGS=backup_cxxflags)

logicc = Builder(
    action='logicc $SOURCES -o $TARGET -L$LOGICC_DIR/dist/connlib.xml -L$LOGICC_DIR/dist/frames.xml -L$LOGICC_DIR/dist/blackbox.xml -- -target x86_64-pc-windows-gnu --std=c++14 -fcomment-block-commands=role -DLOGICC -I$LOGICC_DIR/include -resource-dir /no/such/place -isystem $LOGICC_DIR/ext/llvm-project/build/Release/lib/clang/3.6.0/include -isystem $LOGICC_DIR/ext/llvm-project/libcxx/include -isystem $LOGICC_DIR/sysinclude -isystem $LOGICC_DIR/sysinclude/x86_64-linux-gnu',
    suffix='.lcc',
    src_suffix='.cc')

upduino_dir = os.path.join(inst_dir, 'upduino')
hw_dir = os.path.join('$BUILD_DIR', 'hw')

hw_action = [
    Mkdir(hw_dir),
    Mkdir(os.path.join(hw_dir, 'lib'))
]
for src in glob.glob(upduino_dir+'/*.*',recursive=True):
    hw_action.append(Copy(hw_dir, src))
for src in glob.glob(upduino_dir+'/lib/*.*',recursive=True):
    hw_action.append(Copy(os.path.join(hw_dir, 'lib'), src))

prep = Builder(action=hw_action)

sm = Builder(
    action=[
        'sm -c++ -h$BUILD_DIR/hw -c$LOGICC_DIR/dist/ -a$LOGICC_DIR/architecture/ $SOURCE',
        'conngen -L$LOGICC_DIR/dist -w $SOURCE',
        ],
    suffix='.xhw',
    src_suffix='.lcc')


frammer = Builder(
  action='python $LOGICC_DIR/bin/frammer.py -o $BUILD_DIR/hw/upduino_sys.v $LOGICC_DIR/dist/frames.xml $SOURCE $BUILD_DIR/hw/upduino_sys.in.v',
  src_suffix='.xic')

hdl = Builder(
    action='hdlgen $SOURCE $TARGET',
    suffix='.vhd',
    src_suffix='.xhw')

ghdl = Builder(
    action=[
        'ghdl -a --ieee=synopsys --work=streamlogic lib\divmod.vhd lib\streamlogic.vhd lib\sxmath.vhd',
        'ghdl -a --ieee=synopsys sampler.vhd spi_master_impl.vhd spi_master.vhd',
        'ghdl -a --ieee=synopsys '+env['PROGNAME']+'_ip.vhd '+env['PROGNAME']+'.vhd upduino_fpga0_cn.vhd upduino_fpga0.vhd'
        ],
    chdir=env.subst(hw_dir)
)

yosys = Builder(
    action='yosys -s synth.s > synthesis.log',
    chdir=env.subst(hw_dir)
)

# nextpnr is built with embedded python and requires the
# PYTHONPATH/PYTHONHOME to point to it's installation path
prn_env = dict(env['ENV'])
prn_env['PYTHONPATH'] = pio.get_package_dir('toolchain-fpgaoss')
prn_env['PYTHONHOME'] = pio.get_package_dir('toolchain-fpgaoss')

pnr = Builder(
    action=[
        'nextpnr-ice40.exe --timing-allow-fail --up5k --package sg48 --freq 48 --json $SOURCE --pcf $BUILD_DIR/hw/upduino.pcf --asc $BUILD_DIR/hw/upduino-pnr.asc',
        'icepack $BUILD_DIR/hw/upduino-pnr.asc $TARGET'
        ],
    suffix='.bin',
    src_suffix='.json',
    ENV=prn_env
)

xcfgen = Builder(
    action = xcf_generator,
)

if 'UPLOADCMD' in env:
    uploadcmd = env['UPLOADCMD']
else:
    uploadcmd = find_radiant() +  ' -infile $SOURCE'

env.Append(BUILDERS={'Prep': prep, 'LogiCC': logicc, 'SM': sm, 'Hdl': hdl,
                     'Frammer': frammer,
                     'Ghdl': ghdl, 'Yosys': yosys, 'Pnr': pnr,
                     'Xcfgen': xcfgen})

#target_bin = env.BuildProgram()

env.ProcessProgramDeps()
env.ProcessProjectDeps()


src_dir = os.path.join(inst_dir, 'libsrc')
Library('core-fusion', glob.glob(src_dir+'/*.cc') +  glob.glob(src_dir+'/*.c'))

target_bin = env.Program(
    os.path.join("$BUILD_DIR", env.subst("$PROGNAME$PROGSUFFIX")),
    env['PIOBUILDFILES'],
    LIBS=['core-fusion'], LIBPATH='.'
)
env.Replace(PIOMAINPROG=target_bin)

setup = env.Prep(os.path.join(hw_dir, 'synth.s'), None)

src_list = env.CollectBuildFiles('$BUILD_SRC_DIR', '$PROJECT_SRC_DIR', env.get('SRC_FILTER'))

lcc = env.LogiCC(
    os.path.join('$BUILD_DIR', env['PROGNAME']+'.lcc'),
    src_list)
env.Depends(lcc, setup)

xhw = env.SM(
    os.path.join('$BUILD_DIR', env['PROGNAME']+'.xhw'),
    lcc)

frame = env.Frammer(
    os.path.join(os.path.join('$BUILD_DIR', 'hw'),'upduino_sys.v'),
    lcc)

vhd = env.Hdl(
    os.path.join(os.path.join('$BUILD_DIR', 'hw'),env['PROGNAME']+'.vhd'),
    xhw)
env.Alias('rtl', [vhd,frame])

work = env.Ghdl(
    os.path.join(os.path.join('$BUILD_DIR', 'hw'),'work-obj93.cf'),
    vhd)
env.Alias('work', work)

netlist = env.Yosys(
    os.path.join(os.path.join('$BUILD_DIR', 'hw'),'upduino.json'),
    [work,frame])
env.Alias('netlist', netlist)

bitstream = env.Pnr(
    os.path.join(os.path.join('$BUILD_DIR', 'hw'),'upduino.bin'),
    netlist)
env.Alias('bitstream', bitstream)

xcf = env.Xcfgen(
    os.path.join('$BUILD_DIR', 'upduino.xcf'),
    bitstream
)
env.Alias('xcf', xcf)

upload = env.Alias('upload', xcf, uploadcmd)
AlwaysBuild(upload)

# This is to disable the built-in checkprogsize target
# (see platformio/builder/main.py)
if 'SIZETOOL' in env:
    del env['SIZETOOL']

install_bin = env.Install(env.subst('$PROJECT_DIR'), target_bin)

Default([install_bin])
